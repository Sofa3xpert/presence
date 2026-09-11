import os

import pytest

from presence.core.secrets import get_secret


def test_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("PRESENCE_TEST_KEY", "from-env")
    (tmp_path / "secrets.env").write_text("PRESENCE_TEST_KEY=from-file\n")
    assert get_secret("PRESENCE_TEST_KEY", tmp_path) == "from-env"


def test_file_fallback_and_permission_warning(monkeypatch, tmp_path):
    monkeypatch.delenv("PRESENCE_TEST_KEY", raising=False)
    path = tmp_path / "secrets.env"
    path.write_text("# comment\nPRESENCE_TEST_KEY = from-file\n")
    os.chmod(path, 0o644)
    with pytest.warns(UserWarning, match="chmod 600"):
        assert get_secret("PRESENCE_TEST_KEY", tmp_path) == "from-file"
    os.chmod(path, 0o600)
    assert get_secret("PRESENCE_TEST_KEY", tmp_path) == "from-file"


def test_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("PRESENCE_NOPE", raising=False)
    assert get_secret("PRESENCE_NOPE", tmp_path) is None
