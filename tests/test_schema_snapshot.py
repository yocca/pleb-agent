import subprocess
from pathlib import Path

import pytest
from psycopg import AsyncConnection

from tests.conftest import SCHEMA, add_venue

ROOT = Path(__file__).parent.parent
PLEB_API = ROOT.parent / "pleb-api"


@pytest.mark.skipif(not (PLEB_API / "internal/db/migrations").is_dir(), reason="pleb-api is not checked out next door")
def test_snapshot_matches_pleb_api_migrations(tmp_path: Path) -> None:
    before = SCHEMA.read_text()
    subprocess.run([str(ROOT / "scripts/sync-schema.sh"), str(PLEB_API)], check=True, capture_output=True)
    after = SCHEMA.read_text()
    if after != before:
        SCHEMA.write_text(before)
        pytest.fail("tests/schema/pleb_api.sql is out of date; run scripts/sync-schema.sh ../pleb-api")


async def test_smoke_insert_and_read_venue(conn: AsyncConnection) -> None:
    venue_id = await add_venue(conn, name="Smoke Bar")
    cur = await conn.execute("SELECT name, crawl_status FROM venues WHERE id = %s", (venue_id,))
    assert await cur.fetchone() == ("Smoke Bar", "pending")
