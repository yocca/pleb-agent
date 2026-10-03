# Spec Delta

## Purpose

Turns the content of a venue page, PDF or menu image into a validated weekly happy hour schedule with deals and a confidence score, spending model calls only where a happy hour is likely.

## ADDED Requirements

### Requirement: Keyword gate before any model call
A text candidate (HTML or PDF text) SHALL be sent to a model only if it contains a happy hour signal: the phrase "happy hour", "HH", "half price" or "drink specials" (case-insensitive), or a time range near a price. Candidates without a signal SHALL be recorded as checked with no model call.

#### Scenario: No signal
- **WHEN** a page's text is a dinner menu with no happy hour wording and no time range
- **THEN** no model is called for that page

#### Scenario: Signal present
- **WHEN** a page says "Happy Hour Mon–Fri 4–7pm, $6 drafts"
- **THEN** the page text is sent for extraction

### Requirement: Text extraction for HTML and PDF
For a gated text candidate, the system SHALL send only the relevant text (the block around the happy hour signal, bounded in size) to the configured text model. It SHALL receive a structured result stating whether a happy hour is present and, if so, its windows and deals.

#### Scenario: HTML happy hour
- **WHEN** the page text is "Happy Hour Monday to Friday 4pm–7pm: $6 drafts, $8 wells"
- **THEN** the result has `is_happy_hour: true`, windows for days 1–5 from 16:00 to 19:00, and both deals with prices

### Requirement: Vision extraction for menu images
An image candidate (an `<img>` near happy hour wording, or an image-only PDF page) SHALL be sent to the configured vision model and SHALL produce the same structured result as text extraction. Images SHALL be processed in memory and not stored by the crawler.

#### Scenario: Image-only menu
- **WHEN** the happy hour page contains only an image of a menu
- **THEN** the image is sent to the vision model and its result is used, and the image bytes are not written to disk or the database

### Requirement: Normalized schedule
Extraction results SHALL be normalized to one window per weekday: `day_of_week` 0–6 (0 = Sunday), 24-hour `start` and `end`, or `all_day`. Day ranges such as "Mon–Fri" SHALL be expanded, and times such as "4–7" with no am/pm SHALL be read as afternoon or evening. A window ending before it starts SHALL be kept as crossing midnight.

#### Scenario: Range expansion
- **WHEN** a result says "Mon-Fri 4-7"
- **THEN** five windows are produced, days 1–5, 16:00–19:00

#### Scenario: Late night
- **WHEN** a result says "Thu–Sat 10pm–1am"
- **THEN** windows for days 4–6 from 22:00 to 01:00 are produced

### Requirement: Invalid results rejected
A result SHALL be rejected (stored as an extraction but not published) when it claims a happy hour but has no valid window, has a window longer than 12 hours that isn't `all_day`, or has a time that can't be parsed.

#### Scenario: Unparseable time
- **WHEN** the model returns a window with start "sometime"
- **THEN** the extraction is recorded with `is_happy_hour: true` and confidence 0, and nothing is published

### Requirement: Confidence score
Every extraction SHALL carry a confidence between 0 and 1. It combines the model's own confidence with penalties for missing days, inferred am/pm and conflicting windows, and the scoring is recorded with the prompt version.

#### Scenario: Ambiguous am/pm lowers confidence
- **WHEN** a result's times had no am/pm and were inferred
- **THEN** its confidence is lower than the same result with explicit am/pm
