"""Polite HTTP fetching: one request at a time per host, robots.txt, and stop-on-block."""

from __future__ import annotations

import asyncio
import time
import urllib.robotparser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT_S = 20.0

# Platforms whose terms forbid scraping. Never fetched, even when a venue links to them.
DENIED_DOMAINS = (
    "google.com",
    "goo.gl",
    "g.page",
    "yelp.com",
    "resy.com",
    "opentable.com",
    "instagram.com",
    "facebook.com",
    "fb.com",
    "tripadvisor.com",
    "tock.com",
)

# Markers of an interstitial bot challenge (not merely a page that embeds a CAPTCHA widget).
CHALLENGE_MARKERS = (
    b"cf-chl",
    b"challenge-platform",
    b"<title>just a moment",
    b"<title>attention required",
    b"px-captcha",
    b"<title>access denied",
)


class BlockedError(Exception):
    """The host refused us (401/403/429 or a bot challenge); stop contacting it."""


class DisallowedError(Exception):
    """robots.txt, the same-site rule or the domain denylist forbids this URL."""


@dataclass
class Page:
    url: str
    status: int
    content_type: str
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def site_key(host: str) -> str:
    return host.removeprefix("www.")


def is_denied(url: str) -> bool:
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in DENIED_DOMAINS)


def same_site(url: str, site: str) -> bool:
    return site_key(host_of(url)) == site_key(site) and urlsplit(url).scheme in ("http", "https")


@dataclass
class _Response:
    status: int
    headers: httpx.Headers
    body: bytes


@dataclass
class _Host:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_request: float | None = None
    robots: urllib.robotparser.RobotFileParser | None = None
    blocked: bool = False


class PoliteFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        user_agent: str,
        min_delay_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._ua = user_agent
        self._min_delay = min_delay_s
        self._clock = clock
        self._sleep = sleep
        self._hosts: dict[str, _Host] = {}

    def _host(self, url: str) -> _Host:
        key = urlsplit(url).netloc.lower()
        return self._hosts.setdefault(key, _Host())

    def is_blocked(self, url: str) -> bool:
        return self._host(url).blocked

    def _delay(self, h: _Host) -> float:
        crawl_delay = h.robots.crawl_delay(self._ua) if h.robots else None
        return max(self._min_delay, float(crawl_delay or 0))

    async def _request(self, h: _Host, url: str) -> _Response:
        """One paced request to a host, with the body capped at MAX_BYTES. The caller holds h.lock."""
        if h.last_request is not None:
            wait = h.last_request + self._delay(h) - self._clock()
            if wait > 0:
                await self._sleep(wait)
        h.last_request = self._clock()
        req = self._client.build_request("GET", url, headers={"User-Agent": self._ua})
        resp = await self._client.send(req, stream=True, follow_redirects=False)
        try:
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    break
                chunks.append(chunk)
        finally:
            await resp.aclose()
        return _Response(resp.status_code, resp.headers, b"".join(chunks))

    async def _load_robots(self, h: _Host, url: str) -> None:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser(robots_url)
        resp = await self._request(h, robots_url)
        # RFC 9309: a 4xx means no restrictions; a 5xx means assume complete disallow.
        if resp.status >= 500:
            rp.parse(["User-agent: *", "Disallow: /"])
        elif resp.status >= 400:
            rp.parse([])
        else:
            rp.parse(resp.body.decode("utf-8", errors="replace").splitlines())
        h.robots = rp

    async def allowed(self, url: str) -> bool:
        """Whether robots.txt lets us fetch url (fetching robots.txt first if needed)."""
        h = self._host(url)
        async with h.lock:
            if h.robots is None:
                await self._load_robots(h, url)
        assert h.robots is not None
        return h.robots.can_fetch(self._ua, url)

    async def get(self, url: str, site: str) -> Page:
        """Fetch a same-site URL, following at most MAX_REDIRECTS same-site redirects."""
        for _ in range(MAX_REDIRECTS + 1):
            if is_denied(url) or not same_site(url, site):
                raise DisallowedError(f"{url} is not on {site}")
            h = self._host(url)
            if h.blocked:
                raise BlockedError(f"{host_of(url)} blocked us earlier in this run")
            if not await self.allowed(url):
                raise DisallowedError(f"robots.txt disallows {url}")
            async with h.lock:
                resp = await self._request(h, url)
            if resp.status in (401, 403, 429) or _is_challenge(resp):
                h.blocked = True
                raise BlockedError(f"{host_of(url)} answered {resp.status}")
            if resp.status in (301, 302, 303, 307, 308) and "location" in resp.headers:
                url = urljoin(url, resp.headers["location"])
                continue
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            return Page(url=url, status=resp.status, content_type=ctype, body=resp.body)
        raise DisallowedError(f"too many redirects from {url}")


def _is_challenge(resp: _Response) -> bool:
    """A bot challenge or CAPTCHA page, which some hosts serve with a 200 or 503."""
    if resp.status not in (200, 503):
        return False
    head = resp.body[:20_000].lower()
    return any(m in head for m in CHALLENGE_MARKERS)
