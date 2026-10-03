# pleb-agent

Python jobs for Pleb, a happy hour finder. The first job, `crawl`, visits venues' own websites, finds the pages likely to state a happy hour, and uses Nebius Token Factory models (text, and vision for menu images) to extract a weekly schedule into Postgres. pleb-api owns the database schema and serves the results.

How the crawler behaves toward sites (robots.txt, terms, pacing, stopping on blocks) is described in [docs/crawling.md](docs/crawling.md).

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
cp .env.example .env   # then set NEBIUS_API_KEY, model IDs and prices
```

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres with pleb-api's schema |
| `NEBIUS_BASE_URL`, `NEBIUS_API_KEY` | Nebius Token Factory's OpenAI-compatible endpoint |
| `PLEB_TEXT_MODEL`, `PLEB_VISION_MODEL` | Model IDs for page text and menu images |
| `PLEB_OUTPUT_MODE` | `tool` (default) or `prompted` for models without tool calling |
| `PLEB_TEXT_PRICE_IN/OUT`, `PLEB_VISION_PRICE_IN/OUT` | USD per million tokens, for the spend estimate |
| `PLEB_CRAWLER_CONTACT` | Public URL sent in the User-Agent, explaining the bot |
| `PLEB_MIN_DELAY_S` | Minimum seconds between requests to one host (default 5) |

## Running a crawl

With pleb-api's compose stack up (`docker compose up` in `../pleb-api`, which publishes Postgres on localhost:5432):

```sh
uv run pleb-agent crawl --dry-run --limit 5     # preview: fetch and extract, write nothing
uv run pleb-agent crawl --venue <venue-uuid>    # one venue
uv run pleb-agent crawl --max-spend-usd 2       # everything, stopping model calls at ~$2
```

Options: `--venue`, `--limit`, `--max-pages` (candidate pages per venue, max 5), `--max-spend-usd` (default 1), `--dry-run`, `--concurrency` (sites at once, default 4). The run ends with a report of each venue's crawl status, the schedule found, any venues left unprocessed by the spend cap, and the estimated spend.

Or through pleb-api's compose, which builds this repo's Dockerfile and reads `.env` from here:

```sh
cd ../pleb-api
docker compose --profile agent run --rm agent crawl --dry-run --limit 5
```

## Development

```sh
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest
```

Database tests start PostGIS with testcontainers, so Docker must be running. To use an existing database instead, set `PLEB_TEST_DATABASE_URL`; the tests drop and recreate its `public` schema, so point it at a throwaway database. Websites are faked with `httpx.MockTransport` and models with Pydantic AI's `FunctionModel`, so tests need no network.

The live tests call Nebius for real and are skipped unless enabled:

```sh
PLEB_LIVE_NEBIUS=1 uv run pytest -m live
```

### Schema snapshot

Tests build the database from `tests/schema/pleb_api.sql`, a copy of pleb-api's migrations. After a pleb-api migration changes, refresh it:

```sh
scripts/sync-schema.sh ../pleb-api
```

A test fails when the snapshot is out of date with a pleb-api checkout next to this repo.

## Specs

This repo uses [OpenSpec](https://github.com/Fission-AI/OpenSpec) for spec-driven changes. Project context and sourcing rules are in `openspec/config.yaml`; current specs are in `openspec/specs/`, proposed changes in `openspec/changes/`. In Claude Code, use `/opsx:propose`, `/opsx:apply` and `/opsx:archive`.
