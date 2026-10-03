"""Deterministic normalization of model output into weekday windows, plus confidence scoring."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from datetime import time

from pleb_agent.extract.models import Deal, MenuExtraction

MAX_WINDOW_MINUTES = 12 * 60

# Confidence penalties (see design.md); tuned later against field-photo ground truth.
PENALTY_INFERRED_AMPM = 0.85
PENALTY_AMBIGUOUS_DAYS = 0.8
PENALTY_OVERLAP = 0.7

_DAY_NAMES = {
    0: ("sun", "sunday", "sundays", "su"),
    1: ("mon", "monday", "mondays", "mo", "m"),
    2: ("tue", "tues", "tuesday", "tuesdays", "tu"),
    3: ("wed", "weds", "wednesday", "wednesdays", "we", "w"),
    4: ("thu", "thur", "thurs", "thursday", "thursdays", "th"),
    5: ("fri", "friday", "fridays", "fr", "f"),
    6: ("sat", "saturday", "saturdays", "sa"),
}
_DAY_LOOKUP = {name: day for day, names in _DAY_NAMES.items() for name in names}
_GROUPS = {
    "daily": range(7),
    "everyday": range(7),
    "every day": range(7),
    "7 days": range(7),
    "all week": range(7),
    "weekdays": range(1, 6),
    "weekday": range(1, 6),
    "weekends": (0, 6),
    "weekend": (0, 6),
}
_RANGE_SEP = r"\s*(?:-|–|—|to|thru|through|until)\s*"
_FILLER = {"and", "&", "every", "each", "on", "from", "only", "the", "nightly", "night", "nights"}


class NormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class Window:
    day_of_week: int  # 0 = Sunday
    start: time | None
    end: time | None
    all_day: bool = False

    def minutes(self) -> tuple[int, int]:
        assert self.start is not None and self.end is not None
        s = self.start.hour * 60 + self.start.minute
        e = self.end.hour * 60 + self.end.minute
        return s, e if e > s else e + 24 * 60


@dataclass
class Schedule:
    valid: bool
    windows: list[Window] = field(default_factory=list)
    deals: list[Deal] = field(default_factory=list)
    notes: str | None = None
    confidence: float = 0.0
    problems: list[str] = field(default_factory=list)


def parse_days(text: str) -> tuple[list[int], bool]:
    """Days named in text, and whether any part of it was not understood."""
    s = text.lower().strip().rstrip(".")
    for name, days in sorted(_GROUPS.items(), key=lambda kv: -len(kv[0])):
        if name in s:
            rest = s.replace(name, " ")
            extra, ambiguous = parse_days(rest) if rest.strip(" ,&") else ([], False)
            return sorted(set(days) | set(extra)), ambiguous

    found: set[int] = set()
    ambiguous = False
    # Ranges first: "mon-fri", "thursday through saturday".
    for m in re.finditer(rf"([a-z]+){_RANGE_SEP}([a-z]+)", s):
        a, b = _DAY_LOOKUP.get(m.group(1)), _DAY_LOOKUP.get(m.group(2))
        if a is not None and b is not None:
            found.update((a + i) % 7 for i in range((b - a) % 7 + 1))
            s = s.replace(m.group(0), " ", 1)
    for token in re.split(r"[\s,/&+]+", s):
        token = token.strip(".")
        if not token or token in _FILLER:
            continue
        day = _DAY_LOOKUP.get(token)
        if day is None:
            ambiguous = True
        else:
            found.add(day)
    return sorted(found), ambiguous


_TIME = re.compile(r"^\s*(\d{1,2})(?::?(\d{2}))?\s*(a\.?m?\.?|p\.?m?\.?)?\s*$", re.I)


def parse_time(text: str) -> tuple[int, int, str | None]:
    """(hour, minute, marker) where marker is "am", "pm", "24" or None when unstated."""
    s = text.strip().lower()
    if s in ("noon", "12 noon", "midday"):
        return 12, 0, "pm"
    if s in ("midnight", "12 midnight"):
        return 12, 0, "am"
    m = _TIME.match(s)
    if not m:
        raise NormalizationError(f"cannot read time {text!r}")
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if minute > 59 or hour > 23:
        raise NormalizationError(f"cannot read time {text!r}")
    if m.group(3):
        if not 1 <= hour <= 12:
            raise NormalizationError(f"cannot read time {text!r}")
        return hour, minute, "am" if m.group(3).startswith("a") else "pm"
    if hour == 0 or hour > 12 or (m.group(2) and len(m.group(1)) == 2 and m.group(1).startswith("0")):
        return hour, minute, "24"
    return hour, minute, None


def _to24(hour: int, marker: str) -> int:
    if marker == "24":
        return hour
    return hour % 12 + (12 if marker == "pm" else 0)


def resolve_window(start: str, end: str) -> tuple[time, time, bool]:
    """24-hour start and end, and whether an am/pm had to be inferred.

    Unstated markers are chosen to give the shortest window of at most 12 hours,
    preferring an evening start: "4-7" is 16:00-19:00, "11-2pm" is 11:00-14:00,
    "10-1am" is 22:00-01:00.
    """
    sh, sm, smark = parse_time(start)
    eh, em, emark = parse_time(end)
    inferred = smark is None or emark is None
    options = []
    for s_opt, e_opt in itertools.product([smark] if smark else ["pm", "am"], [emark] if emark else ["pm", "am"]):
        s_min = _to24(sh, s_opt) * 60 + sm
        e_min = _to24(eh, e_opt) * 60 + em
        duration = (e_min - s_min) % (24 * 60)
        if 0 < duration <= MAX_WINDOW_MINUTES:
            options.append((duration, s_opt != "pm", s_min, e_min))
    if not options:
        if not inferred:
            raise NormalizationError(f"window {start}-{end} is empty or longer than 12 hours")
        raise NormalizationError(f"cannot make sense of window {start}-{end}")
    _, _, s_min, e_min = min(options)
    return time(s_min // 60, s_min % 60), time(e_min // 60 % 24, e_min % 60), inferred


def normalize(ex: MenuExtraction) -> Schedule:
    """Turn a model result into weekday windows with a final confidence.

    A result claiming a happy hour is rejected (valid=False, confidence 0) if any window
    can't be read, is longer than 12 hours, or no window remains.
    """
    if not ex.is_happy_hour:
        return Schedule(valid=False, confidence=ex.confidence, notes=ex.notes)

    windows: list[Window] = []
    problems: list[str] = []
    inferred = ambiguous = False
    for raw in ex.windows:
        days, amb = parse_days(raw.days)
        ambiguous |= amb
        if not days:
            problems.append(f"no days in {raw.days!r}")
            continue
        if raw.all_day:
            windows.extend(Window(d, None, None, all_day=True) for d in days)
            continue
        if raw.start is None or raw.end is None:
            problems.append(f"window on {raw.days!r} lacks a start or end")
            continue
        try:
            start, end, inf = resolve_window(raw.start, raw.end)
        except NormalizationError as e:
            problems.append(str(e))
            continue
        inferred |= inf
        windows.extend(Window(d, start, end) for d in days)

    if problems or not windows:
        return Schedule(
            valid=False, deals=ex.deals, notes=ex.notes, confidence=0.0, problems=problems or ["no valid window"]
        )

    confidence = ex.confidence
    if inferred:
        confidence *= PENALTY_INFERRED_AMPM
    if ambiguous:
        confidence *= PENALTY_AMBIGUOUS_DAYS
    if _overlaps(windows):
        confidence *= PENALTY_OVERLAP
    unique = sorted(set(windows), key=lambda w: (w.day_of_week, w.start or time(0)))
    return Schedule(
        valid=True, windows=unique, deals=ex.deals, notes=ex.notes, confidence=round(min(max(confidence, 0.0), 1.0), 3)
    )


def _overlaps(windows: list[Window]) -> bool:
    by_day: dict[int, list[Window]] = {}
    for w in set(windows):
        by_day.setdefault(w.day_of_week, []).append(w)
    for ws in by_day.values():
        if len(ws) < 2:
            continue
        if any(w.all_day for w in ws):
            return True
        spans = sorted(w.minutes() for w in ws)
        if any(b[0] < a[1] for a, b in itertools.pairwise(spans)):
            return True
    return False
