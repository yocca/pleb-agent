# PlebBot: how Pleb crawls venue websites

Pleb is a happy hour finder for bars and restaurants. PlebBot visits a venue's own website to read the happy hour it publishes, so people can find it. This page explains what the bot fetches, how it paces itself, and how to stop it.

**User agent:** `PlebBot/0.1 (+<contact URL>)`. The contact URL is this page, or whatever the operator sets in `PLEB_CRAWLER_CONTACT`.

**Opting out:** add a robots.txt rule, for example

```
User-agent: PlebBot
Disallow: /
```

PlebBot then fetches nothing else from your site. Answering 401, 403 or 429 also stops it for the rest of the run.

## Which sites it visits

- Only the website listed for a venue in Pleb's venue data (from OpenStreetMap and NY State liquor license open data), and the `www.` variant of that host.
- Never third-party listing or social sites, even when a venue's site links to them: Google, Yelp, Resy, OpenTable, Instagram, Facebook, Tripadvisor, Tock and the like. A venue whose listed website is one of these is treated as having no site.
- Redirects are followed only within the same site, at most 3 times.

## What it fetches on a site

1. `robots.txt`, before anything else. Disallowed paths are never fetched. If the homepage is disallowed, the venue is left for manual collection. Per RFC 9309, a missing robots.txt (4xx) allows everything, and a server error (5xx) is treated as "disallow all".
2. The homepage.
3. A terms-of-use page, if the homepage links one (link text or URL matching terms, TOS, legal or conditions). If any sentence in it forbids scraping, crawling, robots, spiders or automated access, the bot stops and fetches nothing more from that site. The check errs toward stopping.
4. `sitemap.xml`.
5. Up to 5 candidate pages from the same site: menus declared in schema.org JSON-LD (`hasMenu`) first, then links and sitemap entries ranked by how strongly they match happy hour, specials, drinks, bar menu or menus.
6. On a page whose text gives no happy hour, up to 3 images that look like a menu (named or captioned happy hour, specials, drinks or menu, or any image on a candidate page), so a photographed menu can be read. Images hosted on another domain are skipped.

Responses are capped at 5 MB with a 20 second timeout.

## Pacing

- One request at a time per host.
- At least 5 seconds between requests to the same host, or the robots.txt `Crawl-delay` if that is longer.
- A run covers a handful of pages per venue and runs occasionally, not continuously.

## When it stops

The bot stops requesting a host for the rest of the run, without retrying from other addresses or with other headers, when the host:

- answers 401, 403 or 429, or
- serves a CAPTCHA or bot challenge page.

The venue is then marked for manual collection.

## What it keeps

- Facts: the happy hour days and times, the discounted items and prices, and the URL of the page they came from, linked from Pleb so people can check the source.
- A SHA-256 of each fetched page, so unchanged pages aren't re-read.
- Page text and images are passed to a language model in memory to read the schedule. They are not stored, and Pleb does not republish menu images.

## Venue statuses

| Status | Meaning |
|---|---|
| `found` | A happy hour was read from the site |
| `not_listed` | The site was crawled and no happy hour was found |
| `manual_only` | robots.txt, the site terms or a block stopped the crawl; collected by hand instead |
| `no_site` | The venue has no website of its own |
