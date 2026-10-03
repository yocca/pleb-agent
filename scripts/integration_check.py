"""End-to-end check: crawl fixture sites into pleb-api's database, then read the result back from the API.

Needs pleb-api's compose stack running (`docker compose up` in ../pleb-api). Serves each directory in
tests/fixtures/sites/ on its own local port, adds a venue per site (plus one without a website), runs the
crawl, and checks crawl statuses and the API's happy hours. Uses canned model answers unless --live is
given, in which case NEBIUS_API_KEY must be set.

    uv run python scripts/integration_check.py [--database-url URL] [--api-url URL] [--live]
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import http.server
import json
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pleb_agent import db  # noqa: E402
from pleb_agent.cli import format_report, run_crawl  # noqa: E402
from pleb_agent.extract.agents import nebius_extractor  # noqa: E402
from pleb_agent.pipeline import Options  # noqa: E402
from pleb_agent.settings import Settings  # noqa: E402
from tests.fakes import NO_HH, FakeModels, hh  # noqa: E402

SITES = ROOT / "tests" / "fixtures" / "sites"
PREFIX = "Integration Check:"
EXPECTED = {"happy": "found", "dinner": "not_listed", "blocked": "manual_only", "terms": "manual_only", None: "no_site"}


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def serve(directory: Path) -> str:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=directory))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/"


def canned(role: str, text: str, images: list[bytes]) -> dict[str, object]:
    if "4pm to 7pm" in text:
        return hh(days="Monday to Friday", start="4pm", end="7pm", confidence=0.92)
    return NO_HH


async def add_venues(database_url: str, sites: dict[str | None, str | None]) -> dict[str | None, UUID]:
    conn = await db.connect(database_url)
    try:
        await conn.execute("DELETE FROM venues WHERE name LIKE %s", (PREFIX + "%",))
        ids = {}
        for i, (key, url) in enumerate(sites.items()):
            cur = await conn.execute(
                """INSERT INTO venues (name, location, timezone, website)
                   VALUES (%s, ST_SetSRID(ST_MakePoint(-74.0037 + %s, 40.7336), 4326)::geography,
                           'America/New_York', %s)
                   RETURNING id""",
                (f"{PREFIX} {key or 'no site'}", i * 0.0001, url),
            )
            row = await cur.fetchone()
            assert row is not None
            ids[key] = UUID(str(row[0]))
        return ids
    finally:
        await conn.close()


async def crawl_statuses(database_url: str, ids: dict[str | None, UUID]) -> dict[str | None, str]:
    conn = await db.connect(database_url)
    try:
        out = {}
        for key, venue_id in ids.items():
            cur = await conn.execute("SELECT crawl_status FROM venues WHERE id = %s", (venue_id,))
            row = await cur.fetchone()
            assert row is not None
            out[key] = str(row[0])
        return out
    finally:
        await conn.close()


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url) as resp:
        data: dict[str, Any] = json.load(resp)
        return data


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", default="postgresql://pleb:pleb@localhost:5432/pleb")
    ap.add_argument("--api-url", default="http://localhost:8080")
    ap.add_argument("--live", action="store_true", help="use Nebius instead of canned answers")
    args = ap.parse_args()

    settings = Settings(database_url=args.database_url, pleb_min_delay_s=0.2)
    sites: dict[str | None, str | None] = {d.name: serve(d) for d in sorted(SITES.iterdir()) if d.is_dir()}
    sites[None] = None
    ids = await add_venues(args.database_url, sites)

    extractor = nebius_extractor(settings) if args.live else FakeModels(canned).extractor()
    failures = []
    for venue_id in ids.values():
        report = await run_crawl(settings, extractor, venue_id=venue_id, limit=None, opts=Options(), max_spend_usd=1.0)
        print(format_report(report, dry_run=False))

    statuses = await crawl_statuses(args.database_url, ids)
    for key, want in EXPECTED.items():
        if statuses.get(key) != want:
            failures.append(f"{key or 'no site'}: crawl_status {statuses.get(key)!r}, want {want!r}")

    venue = await asyncio.to_thread(fetch_json, f"{args.api_url}/v1/venues/{ids['happy']}")
    rows = venue["happy_hours"]
    print(f"API happy hours for {venue['name']}:", json.dumps(rows[:1], indent=2), f"... {len(rows)} rows")
    if sorted(r["day_of_week"] for r in rows) != [1, 2, 3, 4, 5]:
        failures.append(f"API returned days {[r['day_of_week'] for r in rows]}, want Mon-Fri")
    if any(r["source_kind"] != "website" or not r["source_url"].endswith("/happy-hour.html") for r in rows):
        failures.append("API rows lack website provenance")
    if any((r["start"], r["end"]) != ("16:00", "19:00") for r in rows):
        failures.append(f"API times {[(r['start'], r['end']) for r in rows]}, want 16:00-19:00")

    print("\n".join(failures) if failures else "Integration check passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
