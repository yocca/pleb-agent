"""Finding the few pages on a venue site likely to state its happy hour, and its terms page."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin

import lxml.etree
import lxml.html

from pleb_agent.crawl.fetcher import is_denied, same_site

MAX_CANDIDATES = 5

# Link patterns by strength; a link's score is the strongest pattern its URL or text matches.
LINK_PATTERNS: list[tuple[int, re.Pattern[str]]] = [
    (3, re.compile(r"happy[\s_-]*hours?|\bhh\b", re.I)),
    (2, re.compile(r"specials?|drinks?|cocktails?|bar[\s_-]*menu|deals?", re.I)),
    (1, re.compile(r"\bmenus?\b|/menus?(/|$|\.)", re.I)),
]

TERMS_LINK = re.compile(r"terms|\btos\b|legal|conditions", re.I)

# A sentence prohibits automated access when it names the activity and a prohibition.
_ACTIVITY = re.compile(
    r"scrap\w*|crawl\w*|spider\w*|robots?\b|\bbots?\b|automated\s+(means|access|collection|systems?|tools?)", re.I
)
_PROHIBITION = re.compile(r"\bnot\b|prohibit\w*|forbid\w*|may not|must not|shall not|no\s+(one|user)", re.I)


@dataclass(frozen=True)
class Link:
    url: str
    text: str


def parse_links(html: bytes | str, base_url: str) -> list[Link]:
    doc = _parse(html)
    if doc is None:
        return []
    links = []
    for a in doc.iter("a"):
        href = a.get("href")
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = urldefrag(urljoin(base_url, href.strip()))[0]
        links.append(Link(url=url, text=" ".join(a.text_content().split())))
    return links


def jsonld_menu_urls(html: bytes | str, base_url: str) -> list[str]:
    """Menu URLs declared in schema.org JSON-LD (`hasMenu` / `menu`)."""
    doc = _parse(html)
    if doc is None:
        return []
    urls: list[str] = []
    for script in doc.xpath('//script[@type="application/ld+json"]'):
        try:
            data = json.loads(script.text_content())
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk(data):
            for key in ("hasMenu", "menu"):
                for value in _as_list(node.get(key)):
                    if isinstance(value, str):
                        urls.append(urljoin(base_url, value))
                    elif isinstance(value, dict) and isinstance(value.get("url"), str):
                        urls.append(urljoin(base_url, value["url"]))
    return urls


def sitemap_urls(xml: bytes) -> list[str]:
    try:
        root = lxml.etree.fromstring(xml, parser=lxml.etree.XMLParser(resolve_entities=False, no_network=True))
    except lxml.etree.XMLSyntaxError:
        return []
    return [el.text.strip() for el in root.iter("{*}loc") if el.text]


def score(url: str, text: str = "") -> int:
    for strength, pattern in LINK_PATTERNS:
        if pattern.search(url) or pattern.search(text):
            return strength
    return 0


def choose_candidates(
    homepage_url: str,
    site: str,
    links: list[Link],
    menu_urls: list[str],
    sitemap: list[str],
    limit: int = MAX_CANDIDATES,
) -> list[str]:
    """Rank same-site candidate pages; JSON-LD menus first, then by link strength."""
    scored: dict[str, int] = {}

    def add(url: str, s: int) -> None:
        url = urldefrag(url)[0]
        if s <= 0 or url.rstrip("/") == homepage_url.rstrip("/") or is_denied(url) or not same_site(url, site):
            return
        scored[url] = max(scored.get(url, 0), s)

    for url in menu_urls:
        add(url, 4)
    for link in links:
        add(link.url, score(link.url, link.text))
    for url in sitemap:
        add(url, score(url))
    ranked = sorted(scored, key=lambda u: (-scored[u], u))
    return ranked[:limit]


def terms_link(links: list[Link], site: str) -> str | None:
    for link in links:
        if same_site(link.url, site) and (TERMS_LINK.search(link.text) or TERMS_LINK.search(link.url)):
            return link.url
    return None


def prohibits_automation(text: str) -> str | None:
    """The first sentence forbidding automated access, if any."""
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
        if _ACTIVITY.search(sentence) and _PROHIBITION.search(sentence):
            return " ".join(sentence.split())
    return None


def _parse(html: bytes | str) -> lxml.html.HtmlElement | None:
    try:
        return lxml.html.fromstring(html)
    except (lxml.etree.ParserError, ValueError):
        return None


def _walk(data: object):  # type: ignore[no-untyped-def]
    if isinstance(data, dict):
        yield data
        for v in data.values():
            yield from _walk(v)
    elif isinstance(data, list):
        for v in data:
            yield from _walk(v)


def _as_list(v: object) -> list[object]:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]
