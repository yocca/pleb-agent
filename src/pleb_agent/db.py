"""Reads and writes against pleb-api's schema (see pleb-api docs/schema.md)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pleb_agent.extract.normalize import Schedule

# Higher ranks win; see source precedence in pleb-api docs/schema.md.
SOURCE_RANK = {"owner": 4, "field_photo": 3, "website": 2, "user_upload": 1}
STALE_AFTER = timedelta(days=60)

CrawlStatus = str  # "found" | "not_listed" | "manual_only" | "no_site"


@dataclass(frozen=True)
class Venue:
    id: UUID
    name: str
    website: str | None
    owner_locked: bool


async def connect(url: str) -> AsyncConnection:
    return await AsyncConnection.connect(url, autocommit=True)


async def venues_to_crawl(conn: AsyncConnection, venue_id: UUID | None = None, limit: int | None = None) -> list[Venue]:
    sql = "SELECT id, name, website, owner_locked FROM venues"
    params: list[object] = []
    if venue_id is not None:
        sql += " WHERE id = %s"
        params.append(venue_id)
    sql += " ORDER BY name, id"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        return [Venue(*row) for row in await cur.fetchall()]


async def previous_outcome(conn: AsyncConnection, venue_id: UUID, url: str, sha256: str) -> str | None:
    """Status of an earlier submission of identical content at this URL, if any."""
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT status FROM submissions
               WHERE venue_id = %s AND kind = 'website' AND source_url = %s AND content_sha256 = %s
               ORDER BY created_at DESC LIMIT 1""",
            (venue_id, url, sha256),
        )
        row = await cur.fetchone()
    return row[0] if row else None


async def insert_submission(conn: AsyncConnection, venue_id: UUID, url: str, sha256: str, status: str) -> UUID:
    async with conn.cursor() as cur:
        await cur.execute(
            """INSERT INTO submissions (venue_id, kind, source_url, content_sha256, status)
               VALUES (%s, 'website', %s, %s, %s) RETURNING id""",
            (venue_id, url, sha256, status),
        )
        row = await cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


async def set_submission_status(conn: AsyncConnection, submission_id: UUID, status: str) -> None:
    await conn.execute("UPDATE submissions SET status = %s WHERE id = %s", (status, submission_id))


async def insert_extraction(
    conn: AsyncConnection,
    submission_id: UUID,
    model: str,
    prompt_version: str,
    is_happy_hour: bool,
    confidence: float,
    output: dict[str, object],
) -> UUID:
    async with conn.cursor() as cur:
        await cur.execute(
            """INSERT INTO extractions (submission_id, model, prompt_version, is_happy_hour, confidence, output)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (submission_id, model, prompt_version, is_happy_hour, confidence, Jsonb(output)),
        )
        row = await cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


async def publish_website_schedule(
    conn: AsyncConnection,
    venue_id: UUID,
    schedule: Schedule,
    source_url: str,
    extraction_id: UUID,
    observed_at: datetime,
) -> bool:
    """Replace the venue's happy hours with a website schedule if precedence allows.

    Returns False (and changes nothing) for owner-locked venues, or when the current rows
    come from a higher-ranked source and are newer than STALE_AFTER.
    """
    async with conn.transaction():
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT owner_locked FROM venues WHERE id = %s FOR UPDATE", (venue_id,))
            venue = await cur.fetchone()
            if venue is None or venue["owner_locked"]:
                return False
            await cur.execute("SELECT source_kind, observed_at FROM happy_hours WHERE venue_id = %s", (venue_id,))
            current = await cur.fetchall()
        if current:
            top = max(current, key=lambda r: (SOURCE_RANK[r["source_kind"]], r["observed_at"]))
            fresh = observed_at - top["observed_at"] < STALE_AFTER
            if SOURCE_RANK[top["source_kind"]] > SOURCE_RANK["website"] and fresh:
                return False

        await conn.execute("DELETE FROM happy_hours WHERE venue_id = %s", (venue_id,))
        deals = Jsonb([d.model_dump(exclude_none=True) for d in schedule.deals])
        async with conn.cursor() as cur:
            await cur.executemany(
                """INSERT INTO happy_hours (venue_id, day_of_week, start_time, end_time, all_day, deals, notes,
                                            confidence, verified, source_kind, source_url, observed_at, extraction_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, false, 'website', %s, %s, %s)""",
                [
                    (
                        venue_id,
                        w.day_of_week,
                        w.start,
                        w.end,
                        w.all_day,
                        deals,
                        schedule.notes,
                        schedule.confidence,
                        source_url,
                        observed_at,
                        extraction_id,
                    )
                    for w in schedule.windows
                ],
            )
    return True


async def set_crawl_status(conn: AsyncConnection, venue_id: UUID, status: CrawlStatus) -> None:
    await conn.execute("UPDATE venues SET crawl_status = %s, updated_at = now() WHERE id = %s", (status, venue_id))


def dump(obj: object) -> str:
    return json.dumps(obj, default=str)
