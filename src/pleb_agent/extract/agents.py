"""Text and vision extraction agents on Nebius Token Factory's OpenAI-compatible API."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.output import PromptedOutput
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage

from pleb_agent.extract.content import Image
from pleb_agent.extract.models import MenuExtraction
from pleb_agent.settings import Settings

PROMPT_VERSION = "extract_v1"


def load_prompt(version: str = PROMPT_VERSION) -> str:
    return resources.files("pleb_agent.prompts").joinpath(f"{version}.md").read_text()


@dataclass
class ExtractionRun:
    result: MenuExtraction
    model_name: str
    role: str  # "text" or "vision"
    usage: RunUsage


class Extractor:
    """Runs the text or vision agent and reports which model answered and what it used."""

    def __init__(
        self,
        text_model: Model,
        vision_model: Model,
        text_model_name: str,
        vision_model_name: str,
        prompted_output: bool = False,
    ) -> None:
        output = PromptedOutput(MenuExtraction) if prompted_output else MenuExtraction
        instructions = load_prompt()
        self._text = Agent(text_model, output_type=output, instructions=instructions, retries=2)
        self._vision = Agent(vision_model, output_type=output, instructions=instructions, retries=2)
        self._names = {"text": text_model_name, "vision": vision_model_name}

    async def from_text(self, text: str) -> ExtractionRun:
        run = await self._text.run(f"Website content:\n\n{text}")
        return ExtractionRun(run.output, self._names["text"], "text", run.usage)

    async def from_image(self, image: Image) -> ExtractionRun:
        run = await self._vision.run(
            ["This image is from the venue's website.", BinaryContent(data=image.data, media_type=image.media_type)]
        )
        return ExtractionRun(run.output, self._names["vision"], "vision", run.usage)


def nebius_extractor(settings: Settings) -> Extractor:
    provider = OpenAIProvider(base_url=settings.nebius_base_url, api_key=settings.require_api_key())
    return Extractor(
        OpenAIChatModel(settings.pleb_text_model, provider=provider),
        OpenAIChatModel(settings.pleb_vision_model, provider=provider),
        settings.pleb_text_model,
        settings.pleb_vision_model,
        prompted_output=settings.pleb_output_mode == "prompted",
    )
