"""The per-venue pipeline against fake sites, fake models and a real PostGIS."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from psycopg import AsyncConnection

from pleb_agent import db
from pleb_agent.crawl.fetcher import PoliteFetcher
from pleb_agent.pipeline import Options, Prices, RunReport, SpendTracker, VenueCrawler, VenueResult
from tests.conftest import add_venue
from tests.fakes import NO_HH, Answer, FakeModels, FakeResponse, FakeWeb, hh, image_pdf, png, text_pdf

HOME = "https://tavern.example/"
NOW = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)
NAV = '<nav><a href="/">Home</a><a href="/happy-hour">Happy Hour</a><a href="/menu">Menu</a></nav>'
DINNER = NAV + "<main><h1>Dinner</h1><p>Roast chicken, $24. Steak frites, $32.</p></main>"
HH_PAGE = NAV + "<main><h1>Happy Hour</h1><p>Happy Hour Monday to Friday 4pm–7pm: $6 drafts, $8 wells</p></main>"


def answer_by_text(role: str, text: str, images: list[bytes]) -> Answer:
    return hh() if "4pm" in text or images else NO_HH


def tavern(**pages: FakeResponse | str) -> FakeWeb:
    web = FakeWeb(
        {HOME: NAV + "<main>Welcome to the tavern.</main>", HOME + "happy-hour": HH_PAGE, HOME + "menu": DINNER}
    )
    for path, page in pages.items():
        web.add(HOME + path.replace("_", "-"), page)
    return web


class Harness:
    def __init__(self, database_url: str, web: FakeWeb, answer: Callable[[str, str, list[bytes]], Answer]) -> None:
        self.database_url = database_url
        self.web = web
        self.models = FakeModels(answer)

    def crawler(self, opts: Options | None = None, cap_usd: float = 100.0, prices: float = 1.0) -> VenueCrawler:
        fetcher = PoliteFetcher(self.web.client(), "PlebBot/0.1 (+https://pleb.example/bot)", min_delay_s=0)
        spend = SpendTracker(Prices(prices, prices, prices, prices), cap_usd=cap_usd)
        return VenueCrawler(
            fetcher,
            self.models.extractor(),
            spend,
            opts or Options(),
            lambda: db.connect(self.database_url),
            now=lambda: NOW,
        )

    async def crawl(self, venue_id: UUID, **kw: object) -> VenueResult:
        conn = await db.connect(self.database_url)
        try:
            [venue] = await db.venues_to_crawl(conn, venue_id)
        finally:
            await conn.close()
        return await self.crawler(**kw).process(venue)  # type: ignore[arg-type]


@pytest.fixture
def harness(database_url: str) -> Callable[..., Harness]:
    def make(web: FakeWeb | None = None, answer: Callable[[str, str, list[bytes]], Answer] = answer_by_text) -> Harness:
        return Harness(database_url, web or tavern(), answer)

    return make


async def status_of(conn: AsyncConnection, venue_id: UUID) -> str:
    cur = await conn.execute("SELECT crawl_status FROM venues WHERE id = %s", (venue_id,))
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def count(conn: AsyncConnection, table: str) -> int:
    cur = await conn.execute(f"SELECT count(*) FROM {table}")  # table names are test literals
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def test_happy_hour_page_found_and_published(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    h = harness()
    v = await add_venue(conn)
    result = await h.crawl(v)
    assert result.status == "found" and result.published
    assert result.source_url == HOME + "happy-hour"
    assert await status_of(conn, v) == "found"
    cur = await conn.execute(
        """SELECT h.day_of_week, h.start_time, h.source_kind, h.source_url, h.verified, s.source_url, e.prompt_version
           FROM happy_hours h JOIN extractions e ON e.id = h.extraction_id JOIN submissions s ON s.id = e.submission_id
           WHERE h.venue_id = %s ORDER BY h.day_of_week""",
        (v,),
    )
    rows = await cur.fetchall()
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5]
    assert {r[2:] for r in rows} == {("website", HOME + "happy-hour", False, HOME + "happy-hour", "extract_v1")}
    # Only the gated page went to a model, and it got the happy hour text.
    assert [c.role for c in h.models.calls] == ["text"]
    assert "Monday to Friday 4pm–7pm" in h.models.calls[0].text
    # Every fetched page is a submission; the dinner page was checked with no model call.
    cur = await conn.execute("SELECT source_url, status FROM submissions ORDER BY source_url")
    assert await cur.fetchall() == [
        (HOME, "rejected"),
        (HOME + "happy-hour", "extracted"),
        (HOME + "menu", "rejected"),
    ]


async def test_no_website(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    h = harness()
    v = await add_venue(conn, website=None)
    assert (await h.crawl(v)).status == "no_site"
    assert h.web.requests == []
    assert await status_of(conn, v) == "no_site"


async def test_social_profile_is_not_a_website(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    h = harness()
    v = await add_venue(conn, website="https://www.instagram.com/tavern")
    assert (await h.crawl(v)).status == "no_site"
    assert h.web.requests == []


async def test_robots_disallows_homepage(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    h = harness(tavern(robots_txt=FakeResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")))
    h.web.pages["https://tavern.example/robots.txt"] = h.web.pages.pop(HOME + "robots-txt")
    v = await add_venue(conn)
    assert (await h.crawl(v)).status == "manual_only"
    assert [r.url for r in h.web.requests] == ["https://tavern.example/robots.txt"]
    assert await status_of(conn, v) == "manual_only"


async def test_robots_disallowed_page_skipped(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    web = tavern()
    web.add("https://tavern.example/robots.txt", FakeResponse("User-agent: *\nDisallow: /happy-hour\n"))
    h = harness(web)
    v = await add_venue(conn)
    assert (await h.crawl(v)).status == "not_listed"
    assert not web.fetched(HOME + "happy-hour")


async def test_terms_forbid_scraping(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    web = tavern()
    web.add(HOME, NAV + '<footer><a href="/terms">Terms of Use</a></footer>')
    web.add(HOME + "terms", "<p>You may not use robots, spiders or scrapers to access this site.</p>")
    h = harness(web)
    v = await add_venue(conn)
    result = await h.crawl(v)
    assert result.status == "manual_only"
    assert "robots, spiders or scrapers" in result.reason
    assert [r.url for r in web.requests] == [HOME + "robots.txt", HOME, HOME + "terms"]
    assert h.models.calls == []


async def test_forbidden_response(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    web = tavern(happy_hour=FakeResponse("no", status=403))
    h = harness(web)
    v = await add_venue(conn)
    assert (await h.crawl(v)).status == "manual_only"
    after_block = [r.url for r in web.requests][[r.url for r in web.requests].index(HOME + "happy-hour") + 1 :]
    assert after_block == []
    assert await status_of(conn, v) == "manual_only"


async def test_off_site_menu_not_fetched(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    web = tavern()
    web.add(HOME, '<a href="https://resy.com/cities/ny/tavern">Menu</a><a href="/happy-hour">Happy Hour</a>')
    h = harness(web)
    v = await add_venue(conn)
    assert (await h.crawl(v)).status == "found"
    assert not any("resy.com" in r.url for r in web.requests)


async def test_nothing_found_keeps_existing_happy_hours(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    web = tavern()
    web.add(HOME + "happy-hour", DINNER)
    h = harness(web)
    v = await add_venue(conn)
    await conn.execute(
        """INSERT INTO happy_hours (venue_id, day_of_week, start_time, end_time, confidence, source_kind, observed_at)
           VALUES (%s, 6, '14:00', '16:00', 0.9, 'user_upload', now())""",
        (v,),
    )
    assert (await h.crawl(v)).status == "not_listed"
    assert h.models.calls == []
    assert await count(conn, "happy_hours") == 1
    assert await status_of(conn, v) == "not_listed"


async def test_image_only_menu_goes_to_vision(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    image = png("red")
    web = tavern(happy_hour=NAV + '<main><h1>Happy Hour</h1><img src="/img/hh.png" alt=""></main>')
    web.add(HOME + "img/hh.png", FakeResponse(image, content_type="image/png"))
    h = harness(web)
    v = await add_venue(conn)
    result = await h.crawl(v)
    assert result.status == "found"
    # The "Happy Hour" heading gates the text in, the text model finds no schedule, so the image is read.
    assert [(c.role, c.images) for c in h.models.calls] == [("text", []), ("vision", [image])]
    cur = await conn.execute("SELECT count(*) FROM submissions WHERE object_key IS NOT NULL")
    assert await cur.fetchone() == (0,)


async def test_jsonld_pdf_menu_text(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    ld = {"@context": "https://schema.org", "@type": "BarOrPub", "hasMenu": HOME + "menus/drinks.pdf"}
    web = FakeWeb(
        {
            HOME: f'<script type="application/ld+json">{json.dumps(ld)}</script><p>Welcome</p>',
            HOME + "menus/drinks.pdf": FakeResponse(
                text_pdf("Happy Hour Mon-Fri 4pm-7pm $6"), content_type="application/pdf"
            ),
        }
    )
    h = harness(web)
    v = await add_venue(conn)
    result = await h.crawl(v)
    assert result.status == "found" and result.source_url == HOME + "menus/drinks.pdf"
    assert [c.role for c in h.models.calls] == ["text"]


async def test_image_only_pdf_goes_to_vision(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    web = tavern(happy_hour=FakeResponse(image_pdf(), content_type="application/pdf"))
    h = harness(web)
    v = await add_venue(conn)
    assert (await h.crawl(v)).status == "found"
    assert [c.role for c in h.models.calls] == ["vision"]


async def test_unchanged_page_not_reextracted(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    h = harness()
    v = await add_venue(conn)
    await h.crawl(v)
    extractions = await count(conn, "extractions")
    h.models.calls.clear()
    result = await h.crawl(v)
    assert result.status == "found"
    assert h.models.calls == []
    assert await count(conn, "extractions") == extractions
    assert await count(conn, "happy_hours") == 5


async def test_changed_page_reextracted(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    h = harness()
    v = await add_venue(conn)
    await h.crawl(v)
    h.web.add(HOME + "happy-hour", HH_PAGE + "<p>Now with $5 wine</p>")
    h.models.calls.clear()
    await h.crawl(v)
    assert len(h.models.calls) == 1
    cur = await conn.execute("SELECT count(*) FROM submissions WHERE source_url = %s", (HOME + "happy-hour",))
    assert await cur.fetchone() == (2,)


async def test_field_photo_outranks_website(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    h = harness()
    v = await add_venue(conn)
    await conn.execute(
        """INSERT INTO happy_hours (venue_id, day_of_week, start_time, end_time, confidence, verified, source_kind,
                                    observed_at)
           VALUES (%s, 6, '14:00', '16:00', 1.0, true, 'field_photo', %s)""",
        (v, NOW - timedelta(days=10)),
    )
    result = await h.crawl(v)
    assert result.status == "found" and not result.published
    cur = await conn.execute("SELECT source_kind FROM happy_hours WHERE venue_id = %s", (v,))
    assert await cur.fetchall() == [("field_photo",)]
    assert await count(conn, "extractions") == 1  # the website extraction is still stored


async def test_unparseable_time_recorded_not_published(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    h = harness(answer=lambda role, text, images: hh(start="sometime") if "4pm" in text else NO_HH)
    v = await add_venue(conn)
    result = await h.crawl(v)
    assert result.status == "not_listed"
    cur = await conn.execute("SELECT is_happy_hour, confidence, output->'normalized'->>'valid' FROM extractions")
    assert await cur.fetchall() == [(True, 0.0, "false")]
    assert await count(conn, "happy_hours") == 0


async def test_dry_run_writes_nothing(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    h = harness()
    v = await add_venue(conn)
    result = await h.crawl(v, opts=Options(dry_run=True))
    assert result.status == "found"
    assert result.schedule is not None and len(result.schedule.windows) == 5
    assert await status_of(conn, v) == "pending"
    for table in ("submissions", "extractions", "happy_hours"):
        assert await count(conn, table) == 0


async def test_spend_cap_stops_model_calls(conn: AsyncConnection, harness: Callable[..., Harness]) -> None:
    h = harness()
    venues = [await add_venue(conn, name=f"Bar {i}") for i in range(3)]
    # Each fake call uses 1000 + 200 tokens; at $10/Mtok that is $0.012, over the $0.01 cap.
    crawler = h.crawler(opts=Options(concurrency=1), cap_usd=0.01, prices=10.0)
    conn2 = await db.connect(h.database_url)
    all_venues = await db.venues_to_crawl(conn2)
    await conn2.close()
    report: RunReport = await crawler.run(all_venues)
    assert len(h.models.calls) == 1
    assert [r.venue.name for r in report.results] == ["Bar 0"]
    assert [v.name for v in report.unprocessed] == ["Bar 1", "Bar 2"]
    assert report.spent_usd == pytest.approx(0.012)
    assert [await status_of(conn, v) for v in venues] == ["found", "pending", "pending"]
    # The capped venues left no submissions, so the next run extracts them.
    cur = await conn.execute("SELECT count(*) FROM submissions WHERE status = 'extracted'")
    assert await cur.fetchone() == (1,)


async def test_unreachable_site_left_alone(conn: AsyncConnection, database_url: str) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    h = Harness(database_url, FakeWeb(), answer_by_text)
    h.web.transport = lambda: httpx.MockTransport(fail)  # type: ignore[method-assign]
    v = await add_venue(conn)
    crawler = h.crawler()
    [venue] = await db.venues_to_crawl(conn, v)
    report = await crawler.run([venue])
    assert report.results == [] and report.unprocessed == []
    assert [r.venue.id for r in report.unreachable] == [v]
    assert await status_of(conn, v) == "pending"
