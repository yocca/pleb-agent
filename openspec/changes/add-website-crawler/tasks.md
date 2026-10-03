# Tasks

## 1. Project scaffolding

- [ ] 1.1 Create the uv project (`pyproject.toml`, `src/pleb_agent`, Typer CLI entry point `pleb-agent`), with ruff and mypy configured; verify `uv run pleb-agent --help` lists `crawl` and `uv run ruff check` and `uv run mypy src` pass
- [ ] 1.2 Add `Settings` (pydantic-settings) for DATABASE_URL, NEBIUS_BASE_URL, NEBIUS_API_KEY, PLEB_TEXT_MODEL, PLEB_VISION_MODEL, prices and the crawler user agent, plus `.env.example`; verify a unit test loads settings from env and rejects a missing API key only when a model call is attempted
- [ ] 1.3 Add a Dockerfile (python:3.12-slim + uv) and a GitHub Actions workflow running ruff, mypy and pytest; verify `docker build` succeeds and the image runs `pleb-agent --help`, and CI passes on the PR

## 2. Database access and test harness

- [ ] 2.1 Add `tests/schema/pleb_api.sql` (pleb-api's migration snapshot with a source hash header) and `scripts/sync-schema.sh`; verify the script regenerates an identical file from `../pleb-api`
- [ ] 2.2 Add a pytest fixture that starts PostGIS (testcontainers, or `PLEB_TEST_DATABASE_URL`), applies the snapshot and yields a psycopg connection per test; verify a smoke test inserts and reads a venue
- [ ] 2.3 Implement the repository layer (load venues to crawl, find submission by URL and hash, insert submission and extraction, lock venue, read current happy hour rank and age, replace happy hours, set crawl status); verify integration tests for each function

## 3. Crawling (venue-site-crawl)

- [ ] 3.1 Implement the per-host polite fetcher (serialization, max(5 s, Crawl-delay) spacing, user agent, size, timeout and redirect limits, same-site enforcement, blocked-host tracking on 401/403/429/challenge); verify tests with the local fixture server for pacing, user agent, off-site refusal and stop-on-block
- [ ] 3.2 Implement robots.txt handling (disallowed paths skipped; disallowed homepage means `manual_only`); verify fixture-site tests for both scenarios
- [ ] 3.3 Implement terms detection; verify tests with prohibiting and permissive terms pages, including the matched sentence in the result
- [ ] 3.4 Implement candidate discovery (nav links, sitemap, JSON-LD `hasMenu`, ranking, cap of 5, homepage always included); verify tests for nav link, JSON-LD menu, sitemap entry and the cap
- [ ] 3.5 Document the crawler's rules (what's fetched, politeness, stop conditions, user agent and contact page) in `docs/crawling.md`; verify every requirement in the venue-site-crawl spec is described

## 4. Extraction (happy-hour-extraction)

- [ ] 4.1 Implement content-to-text (trafilatura for HTML, pypdf for PDFs, image detection for image-only menus and text-less PDFs) and the keyword gate with windowing; verify tests for no-signal, signal, PDF text and image-only detection
- [ ] 4.2 Define `MenuExtraction` and the versioned prompt, plus the text and vision agents on the Nebius provider; verify `FunctionModel` tests show the text agent gets gated text, the vision agent gets image bytes, and nothing is written to disk
- [ ] 4.3 Implement normalization (day ranges, am/pm inference, midnight crossing, 12-hour cap, rejection rules) and confidence scoring; verify table-driven tests covering every spec scenario plus edge cases ("noon", "11a-2p", "daily", "all day Sunday")
- [ ] 4.4 Add the opt-in live Nebius smoke test (`PLEB_LIVE_NEBIUS=1`) on a fixture menu text and image, and record the confirmed default model IDs in `.env.example`; verify it is skipped without the flag and passes with it

## 5. Publishing and the CLI (schedule-publishing)

- [ ] 5.1 Implement the per-venue pipeline (submissions with content hash and skip-if-unchanged, extractions, precedence check, atomic replace, provenance, crawl status); verify integration tests for every schedule-publishing scenario except dry run and spend cap
- [ ] 5.2 Implement `pleb-agent crawl` (`--venue`, `--limit`, `--max-pages`, `--max-spend-usd`, `--dry-run`, concurrency) with spend accounting and the run report; verify tests for dry run (no DB writes, printed schedule) and the spend cap (stops and lists unprocessed venues)
- [ ] 5.3 Write the README (setup, env vars, running locally and through pleb-api's compose, tests, schema snapshot sync); verify each documented command runs as written

## 6. Integration check

- [ ] 6.1 Against pleb-api's compose stack with its seed and fixture venues pointed at local fixture sites, run `pleb-agent crawl` with `FunctionModel` (and with live Nebius if a key is available); verify the API then returns the extracted happy hours with `source_kind: website`, and each venue has the expected crawl status
