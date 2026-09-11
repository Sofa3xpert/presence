import pytest

from presence.core.config import ConfigError, load_app, load_profile, load_search

PROFILE_OK = """
identity: {name: Ada Example, email: ada@example.org}
work_authorization: {summary: full right to work, needs_sponsorship: false}
skills: [python]
confirmed: true
"""

APP_OK = """
providers:
  local: {kind: openai-compatible, base_url: "http://localhost:11434/v1"}
agents:
  scout: {provider: local, model: "llama3.1:8b", at: "08:00"}
budget_tokens_per_day: 100000
"""


def test_profile_loads(tmp_path):
    (tmp_path / "profile.yaml").write_text(PROFILE_OK)
    profile = load_profile(tmp_path)
    assert profile.identity.name == "Ada Example"


def test_unconfirmed_profile_refused(tmp_path):
    unconfirmed = PROFILE_OK.replace("confirmed: true", "confirmed: false")
    (tmp_path / "profile.yaml").write_text(unconfirmed)
    with pytest.raises(ConfigError, match="confirmed"):
        load_profile(tmp_path)


def test_missing_file_message_names_file(tmp_path):
    with pytest.raises(ConfigError, match="search.yaml"):
        load_search(tmp_path)


def test_validation_error_names_field(tmp_path):
    (tmp_path / "search.yaml").write_text("locations: [London]\n")  # queries missing
    with pytest.raises(ConfigError, match="queries"):
        load_search(tmp_path)


def test_app_loads_and_checks_provider_refs(tmp_path):
    (tmp_path / "presence.yaml").write_text(APP_OK)
    app = load_app(tmp_path)
    assert app.agents["scout"].model == "llama3.1:8b"

    (tmp_path / "presence.yaml").write_text(APP_OK.replace("provider: local", "provider: cloud"))
    with pytest.raises(ConfigError, match="cloud"):
        load_app(tmp_path)
