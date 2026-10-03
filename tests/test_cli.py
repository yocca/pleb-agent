import pytest
from psycopg import AsyncConnection
from typer.testing import CliRunner

from pleb_agent.cli import app, format_report, run_crawl
from pleb_agent.pipeline import Options
from pleb_agent.settings import Settings
from tests.conftest import add_venue
from tests.fakes import NO_HH, FakeModels, FakeWeb, hh

HOME = "https://tavern.example/"


def site() -> FakeWeb:
    return FakeWeb(
        {
            HOME: '<a href="/happy-hour">Happy Hour</a>',
            HOME + "happy-hour": "<h1>Happy Hour</h1><p>Mon-Fri 4pm-7pm, $6 drafts</p>",
        }
    )


async def test_dry_run_prints_schedule_and_writes_nothing(conn: AsyncConnection, database_url: str) -> None:
    await add_venue(conn, name="The Test Tavern")
    await add_venue(conn, name="No Site Bar", website=None)
    models = FakeModels(lambda role, text, images: hh(notes=None) if "4pm" in text else NO_HH)
    settings = Settings(_env_file=None, database_url=database_url, pleb_min_delay_s=0)  # type: ignore[call-arg]
    report = await run_crawl(
        settings,
        models.extractor(),
        venue_id=None,
        limit=None,
        opts=Options(dry_run=True),
        max_spend_usd=1.0,
        transport=site().transport(),
    )
    out = format_report(report, dry_run=True)
    assert out.splitlines()[:7] == [
        "Dry run: 2 venue(s) crawled",
        "- No Site Bar: would be no_site (no venue-owned website)",
        "- The Test Tavern: would be found",
        f"    source: {HOME}happy-hour (confidence 0.90)",
        "    Mon 16:00-19:00",
        "    Tue 16:00-19:00",
        "    Wed 16:00-19:00",
    ]
    assert "    deal: Draft beer $5" in out
    for table in ("submissions", "extractions", "happy_hours"):
        cur = await conn.execute(f"SELECT count(*) FROM {table}")
        assert await cur.fetchone() == (0,)
    cur = await conn.execute("SELECT DISTINCT crawl_status FROM venues")
    assert await cur.fetchall() == [("pending",)]


async def test_report_lists_unprocessed_venues_at_spend_cap(conn: AsyncConnection, database_url: str) -> None:
    for i in range(3):
        await add_venue(conn, name=f"Bar {i}")
    models = FakeModels(lambda role, text, images: hh() if "4pm" in text else NO_HH)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, database_url=database_url, pleb_min_delay_s=0, pleb_text_price_in=10, pleb_text_price_out=10
    )
    report = await run_crawl(
        settings,
        models.extractor(),
        venue_id=None,
        limit=None,
        opts=Options(concurrency=1),
        max_spend_usd=0.02,
        transport=site().transport(),
    )
    out = format_report(report, dry_run=False)
    # Bar 0 costs two calls ($0.012 each: its homepage's "Happy Hour" link passes the gate, then the page itself).
    assert "- Bar 0: is found" in out
    assert "Spend cap reached; 2 venue(s) left unprocessed:" in out
    assert "  - Bar 1 (" in out and "  - Bar 2 (" in out
    assert out.endswith("Estimated model spend: $0.0240")


def test_help_lists_crawl() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "crawl" in result.output


def test_crawl_without_api_key_fails_clearly(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    result = CliRunner().invoke(app, ["crawl", "--dry-run"])
    assert result.exit_code != 0
    assert "NEBIUS_API_KEY" in str(result.exception)
