# venue-site-crawl Specification

## Purpose
Decides whether a venue's own website may be crawled and finds the few pages likely to state its happy hour, while staying polite and stopping when a site objects.

## Requirements

### Requirement: Only venue-owned sites are crawled
The crawler SHALL fetch only pages on the host of the venue's stored `website` (and its `www.` variant), and SHALL NOT fetch Google, Yelp, Resy, OpenTable, Instagram or Facebook pages, even when a venue site links to them.

#### Scenario: Off-site link ignored
- **WHEN** a venue homepage links its menu to a page on resy.com
- **THEN** that page is not fetched

#### Scenario: Venue without a website
- **WHEN** a venue has no stored website
- **THEN** nothing is fetched and the venue's crawl status becomes `no_site`

### Requirement: robots.txt is honored
Before fetching any page on a host, the crawler SHALL read that host's robots.txt and SHALL NOT fetch paths it disallows for the Pleb user agent. If robots.txt disallows the venue's homepage, the venue SHALL be marked `manual_only`.

#### Scenario: Disallowed path
- **WHEN** robots.txt disallows `/menus/` for all agents
- **THEN** no URL under `/menus/` is fetched

#### Scenario: Whole site disallowed
- **WHEN** robots.txt disallows `/` for all agents
- **THEN** no page is fetched and the venue is marked `manual_only`

### Requirement: Site terms are respected
The crawler SHALL look for a terms-of-use page linked from the homepage. If that page prohibits automated access (scraping, crawling, robots or automated collection), the crawler SHALL stop crawling that site and mark the venue `manual_only`.

#### Scenario: Terms forbid scraping
- **WHEN** the homepage links a terms page saying "you may not use robots, spiders or scrapers to access this site"
- **THEN** no further pages on that site are fetched and the venue is marked `manual_only`

### Requirement: Polite request pacing
The crawler SHALL send at most one request at a time per host. It SHALL wait at least 5 seconds between requests to the same host, or the robots.txt `Crawl-delay` if that is larger. Every request SHALL carry a user agent naming Pleb with a contact URL.

#### Scenario: Crawl-delay larger than default
- **WHEN** robots.txt sets `Crawl-delay: 10`
- **THEN** consecutive requests to that host are at least 10 seconds apart

#### Scenario: Identified requests
- **WHEN** any page is fetched
- **THEN** the request's User-Agent contains `PlebBot` and a contact URL

### Requirement: Stop on block
If a host answers 401, 403 or 429, or serves a CAPTCHA or bot challenge, the crawler SHALL stop requesting that host for the rest of the run, SHALL NOT retry with different headers or addresses, and SHALL mark the venue `manual_only`.

#### Scenario: Forbidden response
- **WHEN** a page request returns 403
- **THEN** no further requests go to that host in this run and the venue is marked `manual_only`

### Requirement: Candidate page discovery
For each crawlable site, the crawler SHALL choose at most 5 candidate pages from the homepage, `sitemap.xml`, and same-site links whose URL or link text matches happy-hour, specials, drinks, bar menu or menus patterns. It SHALL also use any menu URL declared in schema.org JSON-LD (`hasMenu`).

#### Scenario: Happy hour page linked from navigation
- **WHEN** the homepage nav links "Happy Hour" to `/happy-hour`
- **THEN** `/happy-hour` is among the candidate pages

#### Scenario: Menu declared in structured data
- **WHEN** the homepage's JSON-LD declares `"hasMenu": "https://venue.example/menus/drinks.pdf"`
- **THEN** that PDF is among the candidate pages

#### Scenario: Page cap
- **WHEN** a site has 20 matching links
- **THEN** at most 5 candidate pages are fetched for that venue
