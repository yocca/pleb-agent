import pytest

from pleb_agent.extract.agents import nebius_extractor
from pleb_agent.settings import MissingAPIKeyError, Settings


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/x")
    monkeypatch.setenv("PLEB_TEXT_MODEL", "some/text-model")
    monkeypatch.setenv("PLEB_VISION_PRICE_OUT", "2.5")
    monkeypatch.setenv("PLEB_CRAWLER_CONTACT", "https://pleb.example/bot")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.database_url == "postgresql://u:p@db:5432/x"
    assert s.pleb_text_model == "some/text-model"
    assert s.pleb_vision_price_out == 2.5
    assert s.user_agent == "PlebBot/0.1 (+https://pleb.example/bot)"


def test_missing_api_key_only_fails_when_a_model_is_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.nebius_api_key is None
    with pytest.raises(MissingAPIKeyError):
        nebius_extractor(s)


def test_api_key_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", "sk-test")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "sk-test" not in repr(s)
    assert s.require_api_key() == "sk-test"
    nebius_extractor(s)
