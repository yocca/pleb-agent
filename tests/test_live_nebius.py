"""Opt-in: real extractions against Nebius Token Factory. Run with PLEB_LIVE_NEBIUS=1 and NEBIUS_API_KEY set."""

import io
import os

import pytest
from PIL import Image, ImageDraw, ImageFont

from pleb_agent.extract import content
from pleb_agent.extract.agents import nebius_extractor
from pleb_agent.extract.normalize import normalize
from pleb_agent.settings import Settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("PLEB_LIVE_NEBIUS") != "1", reason="set PLEB_LIVE_NEBIUS=1 to call Nebius"),
]

MENU = "HAPPY HOUR\nMonday - Friday 4pm - 7pm\n$6 draft beers\n$8 well drinks\n$1 oysters"


def menu_png() -> bytes:
    img = Image.new("RGB", (800, 500), "white")
    draw = ImageDraw.Draw(img)
    draw.multiline_text((40, 40), MENU, fill="black", font=ImageFont.load_default(size=40), spacing=20)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def assert_weekday_4_to_7(schedule_source: object) -> None:
    schedule = normalize(schedule_source)  # type: ignore[arg-type]
    assert schedule.valid, schedule.problems
    assert {w.day_of_week for w in schedule.windows} == {1, 2, 3, 4, 5}
    assert {(w.start.hour, w.end.hour) for w in schedule.windows if w.start and w.end} == {(16, 19)}


async def test_text_extraction() -> None:
    run = await nebius_extractor(Settings()).from_text(MENU)
    assert run.result.is_happy_hour
    assert run.usage.input_tokens > 0
    assert_weekday_4_to_7(run.result)


async def test_vision_extraction() -> None:
    run = await nebius_extractor(Settings()).from_image(content.Image(menu_png(), "image/png"))
    assert run.result.is_happy_hour
    assert_weekday_4_to_7(run.result)
