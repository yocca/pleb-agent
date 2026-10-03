# schedule-publishing Specification

## Purpose
Records what the pipeline fetched and extracted, and decides when a venue's served happy hours and crawl status change, following the shared source-precedence rules.

## Requirements

### Requirement: Every fetched candidate is a submission
Each fetched candidate page SHALL be recorded as a submission with kind `website`, its URL and a SHA-256 of its content. A page whose URL and content hash match an existing submission SHALL NOT be extracted again.

#### Scenario: Unchanged page on re-crawl
- **WHEN** a re-crawl fetches a page whose content hash matches the last submission for that URL
- **THEN** no model is called and no new extraction is created

#### Scenario: Changed page
- **WHEN** the page content changed since the last crawl
- **THEN** a new submission is recorded and extracted

### Requirement: Every model result is an extraction
Each model call's result SHALL be stored as an extraction linked to its submission, with the model name, prompt version, `is_happy_hour`, confidence and the full structured output.

#### Scenario: Audit trail
- **WHEN** a schedule is published from a page
- **THEN** the published happy hours reference the extraction they came from, and that extraction references the submission with the page URL

### Requirement: Source precedence on publish
A website extraction SHALL replace a venue's happy hours only if the venue's current happy hours come from `website` or `user_upload`, or are older than 60 days, or the venue has none. It SHALL NOT replace rows from `owner` or `field_photo` that are 60 days old or newer. Replacement SHALL be atomic: all of the venue's old rows are removed and the new ones inserted in one transaction.

#### Scenario: Field photo outranks website
- **WHEN** a venue has happy hours from a field photo observed 10 days ago and the crawler extracts a different schedule from its website
- **THEN** the venue's happy hours are unchanged, and the website extraction is still stored

#### Scenario: Stale field photo replaced
- **WHEN** the field-photo happy hours were observed 90 days ago
- **THEN** the website schedule replaces them

#### Scenario: Owner-locked venue
- **WHEN** a venue has `owner_locked` set
- **THEN** the crawler does not change its happy hours

### Requirement: Published rows carry provenance
Published happy hours SHALL have `source_kind = 'website'`, the page URL as `source_url`, the crawl time as `observed_at`, the extraction's confidence, and `verified = false`.

#### Scenario: Provenance
- **WHEN** a schedule is published from `https://venue.example/happy-hour`
- **THEN** each published row has that `source_url` and `verified = false`

### Requirement: Crawl status
After a venue is processed, its crawl status SHALL be `found` if a valid happy hour was extracted, `not_listed` if the site was crawled and none was found, `manual_only` if robots, terms or a block stopped the crawl, and `no_site` if it has no website. Finding nothing SHALL NOT delete existing happy hours.

#### Scenario: Nothing found
- **WHEN** a crawlable site has no happy hour on any candidate page
- **THEN** the venue's crawl status is `not_listed` and its existing happy hours remain

### Requirement: Dry run
With `--dry-run`, the pipeline SHALL fetch and extract as usual but SHALL NOT write anything to the database. It SHALL print, per venue, the crawl status it would set and the schedule it would publish.

#### Scenario: Preview
- **WHEN** `pleb-agent crawl --dry-run` runs
- **THEN** the database is unchanged and the output lists each venue's would-be status and schedule

### Requirement: Spend cap
A run SHALL stop making model calls once its estimated model spend reaches `--max-spend-usd`. Remaining venues are left unprocessed and reported.

#### Scenario: Cap reached
- **WHEN** the estimated spend reaches the cap after 40 of 60 venues
- **THEN** no further model calls are made, and the report lists the 20 unprocessed venues
