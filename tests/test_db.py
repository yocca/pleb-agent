from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from psycopg import AsyncConnection

from pleb_agent import db
from pleb_agent.extract.models import MenuExtraction, RawWindow
from pleb_agent.extract.normalize import normalize
from tests.conftest import add_venue

NOW = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)
SCHEDULE = normalize(
    MenuExtraction(
        is_happy_hour=True,
        windows=[RawWindow(days="Mon-Fri", start="4pm", end="7pm")],
        deals=[{"item": "Draft beer", "price": 5}],  # type: ignore[list-item]
        notes="bar only",
        confidence=0.9,
    )
)


async def extraction_for(conn: AsyncConnection, venue_id: UUID) -> UUID:
    sub = await db.insert_submission(conn, venue_id, "https://tavern.example/hh", "abc", "extracted")
    return await db.insert_extraction(conn, sub, "fake-text", "extract_v1", True, 0.9, {"k": "v"})


async def add_hh(conn: AsyncConnection, venue_id: UUID, kind: str, observed_at: datetime) -> None:
    await conn.execute(
        """INSERT INTO happy_hours (venue_id, day_of_week, start_time, end_time, confidence, verified,
                                    source_kind, observed_at)
           VALUES (%s, 6, '14:00', '16:00', 1.0, true, %s, %s)""",
        (venue_id, kind, observed_at),
    )


async def hh_rows(conn: AsyncConnection, venue_id: UUID) -> list[tuple[object, ...]]:
    cur = await conn.execute(
        "SELECT day_of_week, source_kind, verified FROM happy_hours WHERE venue_id = %s ORDER BY day_of_week",
        (venue_id,),
    )
    return await cur.fetchall()


async def test_venues_to_crawl(conn: AsyncConnection) -> None:
    a = await add_venue(conn, name="A Bar")
    await add_venue(conn, name="B Bar", website=None)
    venues = await db.venues_to_crawl(conn)
    assert [v.name for v in venues] == ["A Bar", "B Bar"]
    assert [v.name for v in await db.venues_to_crawl(conn, limit=1)] == ["A Bar"]
    assert [v.id for v in await db.venues_to_crawl(conn, venue_id=a)] == [a]


async def test_submission_lookup_by_url_and_hash(conn: AsyncConnection) -> None:
    v = await add_venue(conn)
    assert await db.previous_outcome(conn, v, "https://tavern.example/hh", "abc") is None
    sub = await db.insert_submission(conn, v, "https://tavern.example/hh", "abc", "rejected")
    assert await db.previous_outcome(conn, v, "https://tavern.example/hh", "abc") == "rejected"
    assert await db.previous_outcome(conn, v, "https://tavern.example/hh", "def") is None
    await db.set_submission_status(conn, sub, "extracted")
    assert await db.previous_outcome(conn, v, "https://tavern.example/hh", "abc") == "extracted"


async def test_publish_with_provenance(conn: AsyncConnection) -> None:
    v = await add_venue(conn)
    ext = await extraction_for(conn, v)
    assert await db.publish_website_schedule(conn, v, SCHEDULE, "https://tavern.example/hh", ext, NOW)
    cur = await conn.execute(
        """SELECT h.source_kind, h.source_url, h.observed_at, h.verified, h.confidence, h.notes, h.deals,
                  s.source_url
           FROM happy_hours h JOIN extractions e ON e.id = h.extraction_id JOIN submissions s ON s.id = e.submission_id
           WHERE h.venue_id = %s""",
        (v,),
    )
    rows = await cur.fetchall()
    assert len(rows) == 5
    assert rows[0] == (
        "website",
        "https://tavern.example/hh",
        NOW,
        False,
        pytest.approx(0.9),
        "bar only",
        [{"item": "Draft beer", "price": 5.0}],
        "https://tavern.example/hh",
    )


async def test_replaces_previous_website_rows(conn: AsyncConnection) -> None:
    v = await add_venue(conn)
    await add_hh(conn, v, "website", NOW - timedelta(days=1))
    await add_hh(conn, v, "user_upload", NOW - timedelta(days=1))
    assert await db.publish_website_schedule(conn, v, SCHEDULE, "u", await extraction_for(conn, v), NOW)
    assert [r[0] for r in await hh_rows(conn, v)] == [1, 2, 3, 4, 5]


@pytest.mark.parametrize("kind", ["field_photo", "owner"])
async def test_recent_higher_source_outranks_website(conn: AsyncConnection, kind: str) -> None:
    v = await add_venue(conn)
    await add_hh(conn, v, kind, NOW - timedelta(days=10))
    assert not await db.publish_website_schedule(conn, v, SCHEDULE, "u", await extraction_for(conn, v), NOW)
    assert await hh_rows(conn, v) == [(6, kind, True)]


async def test_stale_field_photo_replaced(conn: AsyncConnection) -> None:
    v = await add_venue(conn)
    await add_hh(conn, v, "field_photo", NOW - timedelta(days=90))
    assert await db.publish_website_schedule(conn, v, SCHEDULE, "u", await extraction_for(conn, v), NOW)
    assert {r[1] for r in await hh_rows(conn, v)} == {"website"}


async def test_owner_locked_venue_unchanged(conn: AsyncConnection) -> None:
    v = await add_venue(conn, owner_locked=True)
    assert not await db.publish_website_schedule(conn, v, SCHEDULE, "u", await extraction_for(conn, v), NOW)
    assert await hh_rows(conn, v) == []


async def test_set_crawl_status(conn: AsyncConnection) -> None:
    v = await add_venue(conn)
    await db.set_crawl_status(conn, v, "not_listed")
    cur = await conn.execute("SELECT crawl_status FROM venues WHERE id = %s", (v,))
    assert await cur.fetchone() == ("not_listed",)
