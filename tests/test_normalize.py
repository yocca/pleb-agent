from datetime import time

import pytest

from pleb_agent.extract.models import MenuExtraction, RawWindow
from pleb_agent.extract.normalize import (
    PENALTY_AMBIGUOUS_DAYS,
    PENALTY_INFERRED_AMPM,
    PENALTY_OVERLAP,
    normalize,
    parse_days,
    resolve_window,
)

T = time


def extraction(*windows: RawWindow, confidence: float = 1.0) -> MenuExtraction:
    return MenuExtraction(is_happy_hour=True, windows=list(windows), confidence=confidence)


@pytest.mark.parametrize(
    ("text", "days", "ambiguous"),
    [
        ("Mon-Fri", [1, 2, 3, 4, 5], False),
        ("Monday to Friday", [1, 2, 3, 4, 5], False),
        ("Thu–Sat", [4, 5, 6], False),
        ("Fri-Mon", [0, 1, 5, 6], False),
        ("Daily", [0, 1, 2, 3, 4, 5, 6], False),
        ("every day", [0, 1, 2, 3, 4, 5, 6], False),
        ("Weekdays", [1, 2, 3, 4, 5], False),
        ("weekends", [0, 6], False),
        ("Sunday", [0], False),
        ("Tues, Thurs & Sat", [2, 4, 6], False),
        ("Mon-Thu and Sunday", [0, 1, 2, 3, 4], False),
        ("Mon-Fri except holidays", [1, 2, 3, 4, 5], True),
        ("sometimes", [], True),
    ],
)
def test_parse_days(text: str, days: list[int], ambiguous: bool) -> None:
    assert parse_days(text) == (days, ambiguous)


@pytest.mark.parametrize(
    ("start", "end", "expected", "inferred"),
    [
        ("4", "7", (T(16), T(19)), True),
        ("4pm", "7pm", (T(16), T(19)), False),
        ("4:30", "6:30 pm", (T(16, 30), T(18, 30)), True),
        ("16:00", "19:00", (T(16), T(19)), False),
        ("10pm", "1am", (T(22), T(1)), False),
        ("10", "1am", (T(22), T(1)), True),
        ("11a", "2p", (T(11), T(14)), False),
        ("11", "2pm", (T(11), T(14)), True),
        ("noon", "3", (T(12), T(15)), True),
        ("9pm", "midnight", (T(21), T(0)), False),
        ("3 p.m.", "6 p.m.", (T(15), T(18)), False),
    ],
)
def test_resolve_window(start: str, end: str, expected: tuple[time, time], inferred: bool) -> None:
    s, e, inf = resolve_window(start, end)
    assert (s, e) == expected
    assert inf is inferred


def test_range_expansion() -> None:
    s = normalize(extraction(RawWindow(days="Mon-Fri", start="4", end="7")))
    assert s.valid
    assert [(w.day_of_week, w.start, w.end) for w in s.windows] == [(d, T(16), T(19)) for d in range(1, 6)]


def test_late_night_crosses_midnight() -> None:
    s = normalize(extraction(RawWindow(days="Thu–Sat", start="10pm", end="1am")))
    assert [(w.day_of_week, w.start, w.end) for w in s.windows] == [(d, T(22), T(1)) for d in (4, 5, 6)]


def test_all_day_sunday() -> None:
    s = normalize(extraction(RawWindow(days="Sunday", all_day=True)))
    assert s.valid
    assert [(w.day_of_week, w.all_day, w.start) for w in s.windows] == [(0, True, None)]


def test_html_scenario_with_deals() -> None:
    ex = MenuExtraction(
        is_happy_hour=True,
        windows=[RawWindow(days="Monday to Friday", start="4pm", end="7pm")],
        deals=[{"item": "Drafts", "price": 6}, {"item": "Wells", "price": 8}],  # type: ignore[list-item]
        confidence=0.9,
    )
    s = normalize(ex)
    assert s.valid and s.confidence == 0.9
    assert {w.day_of_week for w in s.windows} == {1, 2, 3, 4, 5}
    assert [(d.item, d.price) for d in s.deals] == [("Drafts", 6), ("Wells", 8)]


@pytest.mark.parametrize(
    "window",
    [
        RawWindow(days="Mon-Fri", start="sometime", end="7"),
        RawWindow(days="Mon-Fri", start="4pm", end="close"),
        RawWindow(days="Mon-Fri", start="9am", end="11pm"),  # longer than 12 hours
        RawWindow(days="Mon-Fri", start="4pm", end="4pm"),
        RawWindow(days="Mon-Fri", start="4pm"),
        RawWindow(days="whenever", start="4pm", end="7pm"),
    ],
)
def test_invalid_results_rejected(window: RawWindow) -> None:
    s = normalize(extraction(window, RawWindow(days="Sat", start="2pm", end="4pm")))
    assert not s.valid
    assert s.confidence == 0
    assert s.problems


def test_no_windows_rejected() -> None:
    assert not normalize(extraction()).valid


def test_not_a_happy_hour() -> None:
    s = normalize(MenuExtraction(is_happy_hour=False, confidence=0.95))
    assert not s.valid and s.windows == []


def test_inferred_ampm_lowers_confidence() -> None:
    explicit = normalize(extraction(RawWindow(days="Mon-Fri", start="4pm", end="7pm"), confidence=0.9))
    inferred = normalize(extraction(RawWindow(days="Mon-Fri", start="4", end="7"), confidence=0.9))
    assert inferred.confidence < explicit.confidence
    assert inferred.confidence == pytest.approx(0.9 * PENALTY_INFERRED_AMPM, abs=1e-3)


def test_ambiguous_days_and_overlap_penalties() -> None:
    amb = normalize(extraction(RawWindow(days="Mon-Fri except holidays", start="4pm", end="7pm")))
    assert amb.confidence == pytest.approx(PENALTY_AMBIGUOUS_DAYS)
    overlap = normalize(
        extraction(RawWindow(days="Daily", start="4pm", end="7pm"), RawWindow(days="Fri", start="6pm", end="8pm"))
    )
    assert overlap.confidence == pytest.approx(PENALTY_OVERLAP)


def test_late_night_and_early_windows_do_not_overlap() -> None:
    s = normalize(
        extraction(RawWindow(days="Fri", start="4pm", end="7pm"), RawWindow(days="Fri", start="10pm", end="1am"))
    )
    assert s.valid and s.confidence == 1.0
