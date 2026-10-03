"""The pleb-agent command line."""

from __future__ import annotations

import asyncio
import os
from typing import Annotated
from uuid import UUID

import httpx
import typer

from pleb_agent import db
from pleb_agent.crawl.discover import MAX_CANDIDATES
from pleb_agent.crawl.fetcher import MAX_REDIRECTS, TIMEOUT_S, PoliteFetcher
from pleb_agent.extract.agents import Extractor, nebius_extractor
from pleb_agent.pipeline import Options, Prices, RunReport, SpendTracker, VenueCrawler, VenueResult
from pleb_agent.settings import Settings

# Pydantic AI prints an observability banner on first use; a batch job does not need it.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

app = typer.Typer(help="Jobs that find and extract happy hour schedules for Pleb.", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Keeps `crawl` a subcommand even while it is the only one."""


@app.command()
def crawl(
    venue: Annotated[UUID | None, typer.Option(help="Crawl only this venue id.")] = None,
    limit: Annotated[int | None, typer.Option(min=1, help="Crawl at most this many venues.")] = None,
    max_pages: Annotated[
        int, typer.Option(min=0, max=MAX_CANDIDATES, help="Candidate pages per venue, besides the homepage.")
    ] = MAX_CANDIDATES,
    max_spend_usd: Annotated[
        float, typer.Option(min=0, help="Stop model calls once estimated spend reaches this.")
    ] = 1.0,
    dry_run: Annotated[bool, typer.Option(help="Fetch and extract, but write nothing to the database.")] = False,
    concurrency: Annotated[
        int, typer.Option(min=1, help="Sites crawled at once (each site is still one request at a time).")
    ] = 4,
) -> None:
    """Crawl venue websites for happy hours and publish what is found."""
    settings = Settings()
    report = asyncio.run(
        run_crawl(
            settings,
            nebius_extractor(settings),
            venue_id=venue,
            limit=limit,
            opts=Options(dry_run=dry_run, max_pages=max_pages, concurrency=concurrency),
            max_spend_usd=max_spend_usd,
        )
    )
    typer.echo(format_report(report, dry_run))


async def run_crawl(
    settings: Settings,
    extractor: Extractor,
    *,
    venue_id: UUID | None,
    limit: int | None,
    opts: Options,
    max_spend_usd: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> RunReport:
    conn = await db.connect(settings.database_url)
    try:
        venues = await db.venues_to_crawl(conn, venue_id, limit)
    finally:
        await conn.close()

    spend = SpendTracker(
        Prices(
            settings.pleb_text_price_in,
            settings.pleb_text_price_out,
            settings.pleb_vision_price_in,
            settings.pleb_vision_price_out,
        ),
        cap_usd=max_spend_usd,
    )
    async with httpx.AsyncClient(
        transport=transport, timeout=TIMEOUT_S, follow_redirects=False, max_redirects=MAX_REDIRECTS
    ) as client:
        fetcher = PoliteFetcher(client, settings.user_agent, settings.pleb_min_delay_s)
        crawler = VenueCrawler(fetcher, extractor, spend, opts, lambda: db.connect(settings.database_url))
        return await crawler.run(venues)


def format_report(report: RunReport, dry_run: bool) -> str:
    lines = [f"{'Dry run: ' if dry_run else ''}{len(report.results)} venue(s) crawled"]
    for r in sorted(report.results, key=lambda r: r.venue.name):
        lines.append(_format_result(r, dry_run))
    if report.unreachable:
        lines.append(f"{len(report.unreachable)} venue(s) unreachable, status unchanged:")
        lines += [f"  - {r.venue.name}: {r.reason}" for r in sorted(report.unreachable, key=lambda r: r.venue.name)]
    if report.unprocessed:
        lines.append(f"Spend cap reached; {len(report.unprocessed)} venue(s) left unprocessed:")
        lines += [f"  - {v.name} ({v.id})" for v in sorted(report.unprocessed, key=lambda v: v.name)]
    lines.append(f"Estimated model spend: ${report.spent_usd:.4f}")
    return "\n".join(lines)


_DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _format_result(r: VenueResult, dry_run: bool) -> str:
    verb = "would be" if dry_run else "is"
    out = [f"- {r.venue.name}: {verb} {r.status}" + (f" ({r.reason})" if r.reason else "")]
    if r.schedule is not None:
        out.append(f"    source: {r.source_url} (confidence {r.schedule.confidence:.2f})")
        for w in r.schedule.windows:
            hours = "all day" if w.all_day else f"{w.start:%H:%M}-{w.end:%H:%M}"
            out.append(f"    {_DAYS[w.day_of_week]} {hours}")
        for d in r.schedule.deals:
            price = f" ${d.price:g}" if d.price is not None else ""
            note = f" ({d.note})" if d.note else ""
            out.append(f"    deal: {d.item}{price}{note}")
        if not dry_run and not r.published:
            out.append("    not published")
    return "\n".join(out)
