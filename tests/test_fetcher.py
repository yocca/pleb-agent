import pytest

from pleb_agent.crawl.fetcher import BlockedError, DisallowedError, PoliteFetcher
from tests.fakes import FakeResponse, FakeWeb

UA = "PlebBot/0.1 (+https://pleb.example/bot)"
SITE = "tavern.example"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def fetcher(web: FakeWeb, clock: FakeClock | None = None, min_delay: float = 5.0) -> PoliteFetcher:
    clock = clock or FakeClock()
    return PoliteFetcher(web.client(), UA, min_delay, clock=clock, sleep=clock.sleep)


async def test_requests_are_paced_and_identified() -> None:
    web = FakeWeb({"https://tavern.example/": "<p>home</p>", "https://tavern.example/menu": "<p>menu</p>"})
    clock = FakeClock()
    f = fetcher(web, clock)
    await f.get("https://tavern.example/", SITE)
    await f.get("https://tavern.example/menu", SITE)
    # robots.txt, homepage, menu: two gaps of at least 5 s
    assert [r.url for r in web.requests] == [
        "https://tavern.example/robots.txt",
        "https://tavern.example/",
        "https://tavern.example/menu",
    ]
    assert clock.sleeps == [5.0, 5.0]
    assert all(r.user_agent == UA for r in web.requests)


async def test_crawl_delay_larger_than_default() -> None:
    web = FakeWeb(
        {
            "https://tavern.example/robots.txt": FakeResponse("User-agent: *\nCrawl-delay: 10\n"),
            "https://tavern.example/": "home",
            "https://tavern.example/menu": "menu",
        }
    )
    clock = FakeClock()
    f = fetcher(web, clock)
    await f.get("https://tavern.example/", SITE)
    await f.get("https://tavern.example/menu", SITE)
    assert clock.sleeps == [10.0, 10.0]


async def test_robots_disallowed_path_not_fetched() -> None:
    web = FakeWeb(
        {
            "https://tavern.example/robots.txt": FakeResponse("User-agent: *\nDisallow: /menus/\n"),
            "https://tavern.example/menus/drinks": "drinks",
        }
    )
    f = fetcher(web)
    assert not await f.allowed("https://tavern.example/menus/drinks")
    with pytest.raises(DisallowedError):
        await f.get("https://tavern.example/menus/drinks", SITE)
    assert not web.fetched("https://tavern.example/menus/drinks")


async def test_robots_server_error_means_disallow_all() -> None:
    web = FakeWeb({"https://tavern.example/robots.txt": FakeResponse("oops", status=503)})
    assert not await fetcher(web).allowed("https://tavern.example/")


async def test_off_site_and_denied_urls_refused() -> None:
    web = FakeWeb()
    f = fetcher(web)
    for url in ("https://resy.com/cities/ny/tavern", "https://other.example/menu", "https://www.instagram.com/x"):
        with pytest.raises(DisallowedError):
            await f.get(url, SITE)
    assert web.requests == []


async def test_www_counts_as_same_site() -> None:
    web = FakeWeb({"https://www.tavern.example/menu": "menu"})
    page = await fetcher(web).get("https://www.tavern.example/menu", SITE)
    assert page.status == 200


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse("forbidden", status=403),
        FakeResponse("slow down", status=429),
        FakeResponse("<html><title>Just a moment...</title></html>", status=503),
        FakeResponse("<script src='/cdn-cgi/challenge-platform/x.js'></script>"),
    ],
)
async def test_block_stops_all_requests_to_host(response: FakeResponse) -> None:
    web = FakeWeb({"https://tavern.example/": response, "https://tavern.example/menu": "menu"})
    f = fetcher(web)
    with pytest.raises(BlockedError):
        await f.get("https://tavern.example/", SITE)
    with pytest.raises(BlockedError):
        await f.get("https://tavern.example/menu", SITE)
    assert not web.fetched("https://tavern.example/menu")


async def test_plain_503_is_not_a_block() -> None:
    web = FakeWeb({"https://tavern.example/": FakeResponse("maintenance", status=503)})
    page = await fetcher(web).get("https://tavern.example/", SITE)
    assert page.status == 503


async def test_redirects_followed_only_on_site() -> None:
    web = FakeWeb(
        {
            "https://tavern.example/hh": FakeResponse(status=301, headers={"location": "/happy-hour"}),
            "https://tavern.example/happy-hour": "hh",
            "https://tavern.example/menu": FakeResponse(status=302, headers={"location": "https://resy.com/menu"}),
        }
    )
    f = fetcher(web)
    page = await f.get("https://tavern.example/hh", SITE)
    assert page.url == "https://tavern.example/happy-hour"
    with pytest.raises(DisallowedError):
        await f.get("https://tavern.example/menu", SITE)
    assert not web.fetched("https://resy.com/menu")


async def test_body_is_capped() -> None:
    web = FakeWeb({"https://tavern.example/big": "x" * (6 * 1024 * 1024)})
    page = await fetcher(web).get("https://tavern.example/big", SITE)
    assert len(page.body) <= 5 * 1024 * 1024


async def test_robots_rule_for_plebbot_by_name() -> None:
    web = FakeWeb(
        {
            "https://tavern.example/robots.txt": FakeResponse("User-agent: PlebBot\nDisallow: /\n\nUser-agent: *\n"),
        }
    )
    assert not await fetcher(web).allowed("https://tavern.example/")
