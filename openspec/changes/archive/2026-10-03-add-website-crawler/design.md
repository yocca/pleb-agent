# Design

## Context

pleb-agent is empty apart from OpenSpec scaffolding. It writes into the schema pleb-api owns (see pleb-api `docs/schema.md`): `venues` (read `website`, `owner_locked`; write `crawl_status`), `submissions`, `extractions` and `happy_hours`. The `visible_happy_hours` view in pleb-api hides rows below 0.6 confidence, so this pipeline stores everything and lets the view decide. See proposal.md for scope.

## Goals / Non-Goals

**Goals:**
- One command that processes the West Village venue list end to end, safe to re-run nightly.
- Every external effect (HTTP request, model spend, DB write) bounded and observable.
- An extraction core that the photo-upload flow can reuse unchanged.

**Non-Goals:**
- Scheduling (cron, k8s CronJob); for now it runs by hand or through `docker compose run`.
- Photo uploads and field photos (a later change reuses `happy-hour-extraction`).
- Schema changes; anything that needs one goes through pleb-api first.

## Decisions

**Python 3.12, uv, a `src/pleb_agent` package, Typer CLI.** It matches the project plan, and uv gives a lockfile with fast CI installs.

**Pydantic AI on Nebius Token Factory's OpenAI-compatible API.** `OpenAIChatModel` with an `OpenAIProvider(base_url=NEBIUS_BASE_URL, api_key=NEBIUS_API_KEY)`. Two agents share one output type (`MenuExtraction`): a text agent (`PLEB_TEXT_MODEL`) and a vision agent (`PLEB_VISION_MODEL`). Models are configuration, not code, because the Token Factory catalog changes. The defaults are a mid-size instruct model for text and a Qwen VL model for vision, confirmed against the live catalog during implementation. If a model lacks tool calling, Pydantic AI's prompted-output mode is used; that's a config switch. *Alternative:* the OpenAI Agents SDK, which is tuned to the Responses API and has less reliable structured output on non-OpenAI models.

**The model extracts; code normalizes.** The model returns raw-ish fields (day phrases, time strings, am/pm present or not, deals, its own confidence). Deterministic Python code expands day ranges, resolves am/pm (times of 1–11 with no marker are treated as pm; 12 is noon), detects midnight crossing, validates, and computes the final confidence. That keeps the testable logic out of the prompt. Prompts live in versioned files (`prompts/extract_v1.md`), and the version is stored on every extraction.

**Confidence** = model self-reported confidence × penalties (0.85 if am/pm was inferred; 0.8 if any day phrase was ambiguous; 0.7 for overlapping windows on one day), clamped to [0, 1]. The constants live in one module and get tuned against the field-photo ground truth later.

**HTTP: httpx with one async client and a per-host scheduler.** An `asyncio.Lock` plus last-request timestamp per host enforces serialization and the delay, and hosts run concurrently (default 4). robots.txt is parsed with `urllib.robotparser`, including crawl-delay. Pages are capped at 5 MB with a 20 s timeout and 3 redirects (same-site only).

**Terms detection** is a deliberately conservative heuristic. Find a homepage link whose text or URL matches `terms|tos|legal|conditions`, fetch it, and look for prohibition phrases (`scrap*`, `crawl*`, `spider*`, `robot*`, `automated (means|access|collection)`) within a sentence containing `not|prohibit|forbid|may not`. A hit means `manual_only`. False positives cost a field visit, while false negatives would breach our sourcing rule, so it errs toward stopping.

**Candidate discovery** ranks links by match strength (`happy.?hour` > `specials|drinks|bar.?menu` > `menus?`). It also takes JSON-LD `hasMenu` and `menu` URLs and sitemap entries matching the same patterns, de-duplicates, and keeps the top 5. The homepage is always processed too, since many sites put the happy hour in a homepage banner.

**Content handling.** HTML is converted to text with trafilatura (boilerplate removed, falling back to raw text). PDFs go through pypdf for text; a PDF with no text layer has its pages rendered to images with pypdfium2 for the vision path. The gate window is ±1,500 characters around each signal, merged, and capped at 6,000 characters per call.

**Change detection by content hash only.** The schema has no ETag or Last-Modified columns, and adding them isn't worth a cross-repo change now. Each URL is re-fetched on each run (the polite delays keep this cheap), and extraction is skipped when `(venue_id, source_url, content_sha256)` already exists in `submissions`.

**Publishing in one transaction per venue.** `SELECT ... FOR UPDATE` on the venue row, apply the precedence check, `DELETE` that venue's `happy_hours`, `INSERT` the new rows, and set `crawl_status`. The precedence rank is `owner(4) > field_photo(3) > website(2) > user_upload(1)`, using the highest-ranked existing row and its `observed_at`.

**Spend accounting** uses the token usage Pydantic AI reports, times prices from configuration. As built, prices are one in/out pair per role (`PLEB_TEXT_PRICE_IN/OUT`, `PLEB_VISION_PRICE_IN/OUT`) rather than per model, since each role uses one configured model; the defaults are deliberately high so an unconfigured run overestimates. The cap is checked before each model call, so concurrent sites can overshoot it by at most one call each.

**Testing without the internet.**
- Sites are faked in unit and pipeline tests with `httpx.MockTransport` (fixed responses by URL, every request recorded), one small fake venue site per behavior (robots disallow, terms prohibition, 403, JSON-LD menu, PDF menu, image menu, no happy hour). That replaces the planned local HTTP server: no ports or threads, and pacing is tested with an injected clock. The integration check (`scripts/integration_check.py`) does serve fixture directories over real local HTTP.
- Models are replaced with Pydantic AI's `FunctionModel`, which returns canned structured output keyed by input. That checks prompts are sent only past the gate, and that dry run and the spend cap behave.
- The database is PostGIS via testcontainers (or `PLEB_TEST_DATABASE_URL`). The schema comes from a snapshot of pleb-api's migration (`tests/schema/pleb_api.sql`), refreshed with `scripts/sync-schema.sh ../pleb-api`. A test fails if the snapshot's header hash doesn't match the file it was copied from, when that file is present.
- One opt-in live test (`PLEB_LIVE_NEBIUS=1`) runs a real extraction against Nebius to confirm the endpoint, model names and structured output work. CI skips it unless the secret is configured.

**Packaging:** a Dockerfile (python:3.12-slim + uv) so pleb-api's compose `agent` profile works: `docker compose --profile agent run agent crawl --dry-run`.

## Risks / Trade-offs

- [Terms heuristic marks too many sites `manual_only`] → Report lists them with the matched sentence, and Andrew can review. The constraint is intentional.
- [Model misreads times or days] → Normalization is deterministic and tested, confidence penalties push doubtful rows under the 0.6 visibility line, and extractions are kept for re-processing when prompts improve.
- [Schema drift between the pleb-api migration and the snapshot] → Hash check in tests plus the sync script. A shared migrations package can come later if it bites.
- [Nebius model names or structured-output support change] → Models are configuration, and the live smoke test catches it.
- [Sites built with JS that render nothing in plain HTML] → They're reported as `not_listed`. A headless browser fallback is a follow-up change, since it adds a heavy dependency.

**Notes from implementation:**
- Images are looked at whenever a page's text gives no happy hour, not only when the text has no signal: a "Happy Hour" nav link or heading over a menu photo passes the gate, the text model finds nothing, and the image holds the schedule.
- Menu images are fetched only from the venue's own site, like pages. Images served from a site builder's CDN (Squarespace, Wix and similar) are skipped for now; allowing a short list of builder CDNs is a possible follow-up.
- Submissions are written after extraction, so a run stopped by the spend cap leaves no record of the page and the next run extracts it. An unchanged page that produced a happy hour before keeps the venue `found` without re-publishing.
- A site that can't be reached (DNS, connection or timeout errors) leaves the venue's crawl status unchanged and is listed in the report, so it is retried next run.

## Open Questions

- Default Nebius model IDs and prices: confirmed from the live catalog when the live test first runs. They're configuration and don't change the design.
