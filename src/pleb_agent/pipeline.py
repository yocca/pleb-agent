"""The website pipeline: crawl a venue's site, extract its happy hour, publish it."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin
from uuid import UUID

import httpx
from psycopg import AsyncConnection

from pleb_agent import db
from pleb_agent.crawl import discover
from pleb_agent.crawl.fetcher import BlockedError, DisallowedError, Page, PoliteFetcher, host_of, is_denied
from pleb_agent.extract import content
from pleb_agent.extract.agents import PROMPT_VERSION, ExtractionRun, Extractor
from pleb_agent.extract.normalize import Schedule, normalize

# Submission statuses (pleb-api schema): "extracted" means a valid happy hour came from it.
FOUND, NOTHING = "extracted", "rejected"


UNCHANGED_FOUND = object()  # a page that yielded a happy hour last time and hasn't changed


class SpendCapReachedError(Exception):
    pass


@dataclass
class Prices:
    """USD per million tokens."""

    text_in: float
    text_out: float
    vision_in: float
    vision_out: float


@dataclass
class SpendTracker:
    prices: Prices
    cap_usd: float
    spent_usd: float = 0.0

    def check(self) -> None:
        if self.spent_usd >= self.cap_usd:
            raise SpendCapReachedError

    def charge(self, run: ExtractionRun) -> None:
        p_in, p_out = (
            (self.prices.text_in, self.prices.text_out)
            if run.role == "text"
            else (self.prices.vision_in, self.prices.vision_out)
        )
        self.spent_usd += (run.usage.input_tokens * p_in + run.usage.output_tokens * p_out) / 1_000_000


@dataclass
class VenueResult:
    venue: db.Venue
    status: str | None  # None when the venue was not finished: spend cap, or its site was unreachable
    schedule: Schedule | None = None
    source_url: str | None = None
    published: bool = False
    reason: str = ""
    pages: list[str] = field(default_factory=list)
    unreachable: bool = False


@dataclass
class RunReport:
    results: list[VenueResult]
    spent_usd: float
    unprocessed: list[db.Venue]  # left alone because the spend cap was reached
    unreachable: list[VenueResult]  # network errors; crawl status unchanged, retried next run


@dataclass
class Options:
    dry_run: bool = False
    max_pages: int = discover.MAX_CANDIDATES
    concurrency: int = 4


@dataclass
class _Best:
    schedule: Schedule
    url: str
    extraction_id: UUID | None  # None in a dry run
    run: ExtractionRun


class VenueCrawler:
    def __init__(
        self,
        fetcher: PoliteFetcher,
        extractor: Extractor,
        spend: SpendTracker,
        opts: Options,
        connect: Callable[[], Awaitable[AsyncConnection]],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.fetcher = fetcher
        self.extractor = extractor
        self.spend = spend
        self.opts = opts
        self.connect = connect
        self.now = now

    async def run(self, venues: list[db.Venue]) -> RunReport:
        sem = asyncio.Semaphore(self.opts.concurrency)

        async def one(v: db.Venue) -> VenueResult:
            async with sem:
                return await self.process(v)

        results = await asyncio.gather(*(one(v) for v in venues))
        return RunReport(
            results=[r for r in results if r.status is not None],
            spent_usd=self.spend.spent_usd,
            unprocessed=[r.venue for r in results if r.status is None and not r.unreachable],
            unreachable=[r for r in results if r.unreachable],
        )

    async def process(self, venue: db.Venue) -> VenueResult:
        conn = await self.connect()
        try:
            result = await self._crawl(conn, venue)
            if result.status is not None and not self.opts.dry_run:
                await db.set_crawl_status(conn, venue.id, result.status)
            return result
        finally:
            await conn.close()

    async def _crawl(self, conn: AsyncConnection, venue: db.Venue) -> VenueResult:
        if not venue.website or is_denied(venue.website) or not host_of(venue.website):
            return VenueResult(venue, "no_site", reason="no venue-owned website")
        site = host_of(venue.website)
        home_url = venue.website

        try:
            if not await self.fetcher.allowed(home_url):
                return VenueResult(venue, "manual_only", reason="robots.txt disallows the homepage")
            home = await self.fetcher.get(home_url, site)

            links = discover.parse_links(home.body, home.url)
            terms_url = discover.terms_link(links, site)
            if terms_url:
                terms = await self._get_optional(terms_url, site)
                sentence = discover.prohibits_automation(content.html_to_text(terms.body)) if terms else None
                if sentence:
                    return VenueResult(venue, "manual_only", reason=f"site terms forbid automated access: {sentence}")

            sitemap: list[str] = []
            sm = await self._get_optional(urljoin(home.url, "/sitemap.xml"), site)
            if sm and sm.status == 200:
                sitemap = discover.sitemap_urls(sm.body)
            candidates = discover.choose_candidates(
                home.url, site, links, discover.jsonld_menu_urls(home.body, home.url), sitemap, self.opts.max_pages
            )

            result = VenueResult(venue, None, pages=[home.url, *candidates])
            best: _Best | None = None
            unchanged_found = False
            for url in [None, *candidates]:
                fetched = home if url is None else await self._get_optional(url, site)
                if fetched is None or fetched.status != 200:
                    continue
                found = await self._process_page(conn, venue, fetched, site, is_candidate=url is not None)
                if found is UNCHANGED_FOUND:
                    unchanged_found = True
                elif isinstance(found, _Best) and (
                    best is None or found.schedule.confidence > best.schedule.confidence
                ):
                    best = found
        except BlockedError as e:
            return VenueResult(venue, "manual_only", reason=f"blocked: {e}")
        except DisallowedError as e:
            return VenueResult(venue, "manual_only", reason=str(e))
        except SpendCapReachedError:
            return VenueResult(venue, None, reason="spend cap reached")
        except httpx.HTTPError as e:
            return VenueResult(venue, None, reason=f"site unreachable: {e!r}", unreachable=True)

        if best is None and unchanged_found:
            result.status, result.reason = "found", "happy hour page unchanged since the last crawl"
            return result
        if best is None:
            result.status, result.reason = "not_listed", "no happy hour found on the site"
            return result
        result.status, result.schedule, result.source_url = "found", best.schedule, best.url
        if best.extraction_id is not None:
            result.published = await db.publish_website_schedule(
                conn, venue.id, best.schedule, best.url, best.extraction_id, self.now()
            )
            if not result.published:
                result.reason = "kept existing higher-precedence or owner-locked happy hours"
        return result

    async def _get_optional(self, url: str, site: str) -> Page | None:
        """Fetch a secondary page, skipping it when robots.txt excludes it or it fails to load."""
        try:
            return await self.fetcher.get(url, site)
        except (DisallowedError, httpx.HTTPError):
            return None

    async def _process_page(
        self, conn: AsyncConnection, venue: db.Venue, page: Page, site: str, is_candidate: bool
    ) -> _Best | object | None:
        sha = hashlib.sha256(page.body).hexdigest()
        previous = await db.previous_outcome(conn, venue.id, page.url, sha)
        if previous is not None:
            # Unchanged since the last crawl, so its extraction is already recorded (and published, if found).
            return UNCHANGED_FOUND if previous == FOUND else None

        # Extract before writing anything, so a spend-cap stop leaves no submission behind
        # and the page is extracted on the next run.
        scored = [(run, normalize(run.result)) for run in await self._extract(page, site, is_candidate)]
        valid = [(run, sch) for run, sch in scored if sch.valid]
        best_run, best_schedule = max(valid, key=lambda rs: rs[1].confidence) if valid else (None, None)
        if self.opts.dry_run:
            return _Best(best_schedule, page.url, None, best_run) if best_run and best_schedule else None

        submission_id = await db.insert_submission(conn, venue.id, page.url, sha, FOUND if valid else NOTHING)
        best: _Best | None = None
        for run, schedule in scored:
            extraction_id = await self._record_extraction(conn, submission_id, run, schedule)
            if run is best_run and schedule is best_schedule:
                best = _Best(schedule, page.url, extraction_id, run)
        return best

    async def _extract(self, page: Page, site: str, is_candidate: bool) -> list[ExtractionRun]:
        runs: list[ExtractionRun] = []
        if page.content_type.startswith("image/"):
            return [await self._vision(content.Image(page.body, page.content_type))]

        if page.content_type == "application/pdf":
            text = content.pdf_to_text(page.body)
            if not text:
                for image in content.pdf_to_images(page.body):
                    runs.append(await self._vision(image))
                return runs
        else:
            text = content.html_to_text(page.body)

        gated = content.gate(text)
        if gated is not None:
            self.spend.check()
            run = await self.extractor.from_text(gated)
            self.spend.charge(run)
            runs.append(run)
        # The happy hour may be an image even when the page text mentions it (a "Happy Hour" nav link,
        # or a heading over a menu photo), so look at images whenever the text gave no happy hour.
        if page.content_type != "application/pdf" and not any(r.result.is_happy_hour for r in runs):
            for img_url in content.menu_image_urls(page.body, page.url, is_candidate):
                img = await self._get_optional(img_url, site)
                if img and img.status == 200 and img.content_type.startswith("image/"):
                    runs.append(await self._vision(content.Image(img.body, img.content_type)))
        return runs

    async def _vision(self, image: content.Image) -> ExtractionRun:
        self.spend.check()
        run = await self.extractor.from_image(image)
        self.spend.charge(run)
        return run

    async def _record_extraction(
        self, conn: AsyncConnection, submission_id: UUID, run: ExtractionRun, schedule: Schedule
    ) -> UUID:
        output = run.result.model_dump()
        output["normalized"] = {
            "valid": schedule.valid,
            "problems": schedule.problems,
            "windows": [
                {
                    "day_of_week": w.day_of_week,
                    "start": str(w.start) if w.start else None,
                    "end": str(w.end) if w.end else None,
                    "all_day": w.all_day,
                }
                for w in schedule.windows
            ],
        }
        return await db.insert_extraction(
            conn,
            submission_id,
            run.model_name,
            PROMPT_VERSION,
            run.result.is_happy_hour,
            schedule.confidence,
            output,
        )
