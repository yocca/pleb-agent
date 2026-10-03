from pathlib import Path

import pytest

from pleb_agent.extract.agents import PROMPT_VERSION, load_prompt
from pleb_agent.extract.content import Image
from tests.fakes import FakeModels, hh, png


def test_prompt_is_versioned() -> None:
    assert PROMPT_VERSION == "extract_v1"
    assert "happy hour" in load_prompt()


async def test_text_agent_gets_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    models = FakeModels(lambda role, text, images: hh())
    run = await models.extractor().from_text("Happy Hour Mon-Fri 4-7pm")
    assert run.result.is_happy_hour
    assert run.role == "text"
    assert run.model_name == "fake-text"
    assert run.usage.input_tokens == 1000
    assert models.calls[0].role == "text"
    assert "Happy Hour Mon-Fri 4-7pm" in models.calls[0].text
    assert models.calls[0].images == []


async def test_vision_agent_gets_image_bytes_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    data = png()
    models = FakeModels(lambda role, text, images: hh())
    run = await models.extractor().from_image(Image(data, "image/png"))
    assert run.role == "vision"
    assert models.calls[0].role == "vision"
    assert models.calls[0].images == [data]
    assert list(tmp_path.iterdir()) == []
