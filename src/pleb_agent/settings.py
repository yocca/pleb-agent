"""Configuration, read from the environment (and a local .env file)."""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MissingAPIKeyError(RuntimeError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://pleb:pleb@localhost:5432/pleb"

    # Nebius Token Factory's OpenAI-compatible endpoint.
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    nebius_api_key: SecretStr | None = None
    pleb_text_model: str = "meta-llama/Llama-3.3-70B-Instruct"
    pleb_vision_model: str = "Qwen/Qwen2.5-VL-72B-Instruct"
    # "tool" uses tool calling for structured output; "prompted" asks for JSON in the
    # prompt, for models without tool-calling support.
    pleb_output_mode: Literal["tool", "prompted"] = "tool"

    # USD per million tokens, used for the --max-spend-usd estimate. Defaults are
    # deliberately high so an unconfigured run overestimates its spend rather than underestimating it.
    pleb_text_price_in: float = 1.0
    pleb_text_price_out: float = 3.0
    pleb_vision_price_in: float = 1.0
    pleb_vision_price_out: float = 3.0

    # The crawler identifies itself with this contact URL. It must be a page a
    # site owner can open, explaining the bot and how to opt out.
    pleb_crawler_contact: str = "https://github.com/yocca/pleb-agent/blob/main/docs/crawling.md"
    pleb_min_delay_s: float = 5.0

    @property
    def user_agent(self) -> str:
        return f"PlebBot/0.1 (+{self.pleb_crawler_contact})"

    def require_api_key(self) -> str:
        if self.nebius_api_key is None or not self.nebius_api_key.get_secret_value():
            raise MissingAPIKeyError("NEBIUS_API_KEY is not set; it is needed for model calls")
        return self.nebius_api_key.get_secret_value()
