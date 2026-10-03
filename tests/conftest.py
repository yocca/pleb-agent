"""Shared fixtures. Database tests run against PostGIS: PLEB_TEST_DATABASE_URL if set, else a testcontainer."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from psycopg import AsyncConnection

SCHEMA = Path(__file__).parent / "schema" / "pleb_api.sql"
POSTGIS_IMAGE = "postgis/postgis:16-3.4"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("PLEB_TEST_DATABASE_URL")
    if url:
        _apply_schema(url)
        yield url
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(POSTGIS_IMAGE, username="pleb", password="pleb", dbname="pleb", driver=None) as pg:
        url = pg.get_connection_url()
        _apply_schema(url)
        yield url


def _apply_schema(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.execute(SCHEMA.read_text())  # type: ignore[arg-type]


@pytest.fixture
async def conn(database_url: str) -> AsyncIterator[AsyncConnection]:
    async with await AsyncConnection.connect(database_url, autocommit=True) as c:
        await c.execute("TRUNCATE venues CASCADE")
        yield c


async def add_venue(
    conn: AsyncConnection,
    name: str = "The Test Tavern",
    website: str | None = "https://tavern.example/",
    owner_locked: bool = False,
) -> UUID:
    cur = await conn.execute(
        """INSERT INTO venues (name, location, timezone, website, owner_locked)
           VALUES (%s, ST_SetSRID(ST_MakePoint(-74.0037, 40.7336), 4326)::geography, 'America/New_York', %s, %s)
           RETURNING id""",
        (name, website, owner_locked),
    )
    row = await cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))
