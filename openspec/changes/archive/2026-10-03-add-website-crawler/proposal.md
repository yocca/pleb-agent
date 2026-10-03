# Proposal

## Why

pleb-api can store and serve happy hours, but nothing produces them yet. Most West Village venues publish their happy hour on their own website, so a polite crawler with model-based extraction can cover most of the launch area in one pass. It also leaves a short, specific list of venues for Andrew's field visits.

## What Changes

- New Python project (`pleb-agent`) with a CLI: `pleb-agent crawl` runs the website pipeline over venues in the shared Postgres database.
- **Polite crawling of venue-owned sites only**: robots.txt and crawl-delay are honored, sites whose terms forbid automated access are skipped, there's a per-domain rate limit, and the user agent identifies Pleb with a contact page. Crawling stops on a block.
- **Candidate discovery**: sitemap, homepage links and keyword-matched URLs, plus schema.org JSON-LD, find the 1–5 pages per site likely to hold a happy hour.
- **Extraction**: a free keyword gate first, then a Nebius Token Factory text model for HTML and PDF text, and a vision model only for menu images. Both produce one validated, typed schedule shape.
- **Normalization and validation**: times become 24-hour local windows, day ranges are expanded, impossible values are rejected, and a confidence score is computed.
- **Publishing**: every fetched page is recorded as a submission and every model result as an extraction. A venue's happy hours are replaced only when the source-precedence rules allow it, and each venue gets a crawl status (`found`, `not_listed`, `manual_only` or `no_site`).
- **Run controls**: `--dry-run`, `--venue`, `--max-pages`, `--max-spend-usd`, and model and endpoint configuration through the environment.
- Dockerfile so `docker compose --profile agent run agent crawl` works from pleb-api's compose setup.

## Capabilities

### New Capabilities
- `venue-site-crawl`: deciding whether and how a venue's own website may be crawled, and finding the pages likely to hold its happy hour.
- `happy-hour-extraction`: turning a page's text or menu image into a validated weekly happy hour schedule with deals and a confidence score.
- `schedule-publishing`: recording what was fetched and extracted, and updating a venue's served happy hours and crawl status under the source-precedence rules.

### Modified Capabilities
- None (pleb-agent has no specs yet).

## Impact

- New dependencies: Python 3.12, uv, Pydantic AI (OpenAI-compatible provider pointed at Nebius Token Factory), httpx, psycopg 3, a PDF text extractor and an HTML text extractor.
- Writes to pleb-api's `submissions`, `extractions`, `happy_hours` and `venues.crawl_status`. The schema is unchanged; pleb-api still owns it.
- Costs: Nebius API usage, bounded per run by `--max-spend-usd` and the keyword gate.
- External effect: requests to venue websites. This is the first component that touches the outside world, which is why politeness and the stop-on-block rules are requirements, not defaults.
- Out of scope: photo uploads and field photos (they'll reuse the extraction capability later), scheduling (cron/k8s), and any Google, Yelp, Resy, OpenTable or Instagram source.
