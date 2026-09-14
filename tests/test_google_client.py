"""The shipped Google client: placeholders in source, injected at release, never committed."""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from presence.adapters import google_client as gc

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "src/presence/adapters/google_client.py"
SCRIPT = ROOT / "scripts/inject_google_client.py"

# what a real Google client looks like — none of this may be in the tree
_REAL_ID = re.compile(r"\b\d{6,}-[a-z0-9]{16,}\.apps\.googleusercontent\.com\b")
_SECRET_VALUE = re.compile(r'client_secret"?\s*[:=]\s*"(?!__|\s*")[^"]{8,}"')


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("PRESENCE_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("PRESENCE_GOOGLE_CLIENT_SECRET", raising=False)


def test_source_build_has_no_client(clean_env, monkeypatch):
    monkeypatch.setattr(gc, "CLIENT_ID", "__PRESENCE_GOOGLE_CLIENT_ID__")
    monkeypatch.setattr(gc, "CLIENT_SECRET", "__PRESENCE_GOOGLE_CLIENT_SECRET__")
    assert gc.builtin_client_config() is None and gc.has_builtin_client() is False


def test_env_override_builds_an_installed_config(clean_env, monkeypatch):
    monkeypatch.setenv("PRESENCE_GOOGLE_CLIENT_ID", "1-abc.apps.googleusercontent.com")
    monkeypatch.setenv("PRESENCE_GOOGLE_CLIENT_SECRET", "s3cret-for-tests")
    cfg = gc.builtin_client_config()
    assert cfg["installed"]["client_id"] == "1-abc.apps.googleusercontent.com"
    assert cfg["installed"]["client_secret"] == "s3cret-for-tests"
    assert cfg["installed"]["auth_uri"].startswith("https://accounts.google.com/")
    assert cfg["installed"]["token_uri"] == "https://oauth2.googleapis.com/token"
    assert cfg["installed"]["redirect_uris"] == ["http://localhost"]
    monkeypatch.delenv("PRESENCE_GOOGLE_CLIENT_SECRET")
    monkeypatch.setattr(gc, "CLIENT_SECRET", "__PRESENCE_GOOGLE_CLIENT_SECRET__")
    assert "client_secret" not in gc.builtin_client_config()["installed"]


def test_no_google_secret_anywhere_in_the_tree():
    """A release build injects the client; the repository must never carry one."""
    offenders = []
    for folder in ("src", "scripts", "tests", "docs"):
        for path in (ROOT / folder).rglob("*"):
            if not path.is_file() or path.suffix in {".pdf", ".png", ".db", ".pyc"}:
                continue
            text = path.read_text(errors="ignore")
            if ("GOCSPX" + "-") in text or _REAL_ID.search(text) or _SECRET_VALUE.search(text):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
    src = MODULE.read_text()
    assert 'CLIENT_ID = "__PRESENCE_GOOGLE_CLIENT_ID__"' in src
    assert 'CLIENT_SECRET = "__PRESENCE_GOOGLE_CLIENT_SECRET__"' in src


def _load(path: Path):
    spec = importlib.util.spec_from_file_location("gc_copy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(args, env, copy):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--file", str(copy), *args],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )


def test_injection_script_replaces_both_placeholders_and_is_idempotent(tmp_path, clean_env):
    copy = tmp_path / "google_client.py"
    shutil.copy(MODULE, copy)
    env = {
        "PRESENCE_GOOGLE_CLIENT_ID": "1-abc.apps.googleusercontent.com",
        "PRESENCE_GOOGLE_CLIENT_SECRET": "s3cret-for-tests",
    }
    assert _run(["--check"], {}, copy).returncode == 1
    missing = _run([], {"PRESENCE_GOOGLE_CLIENT_ID": ""}, copy)
    assert missing.returncode == 2 and "not set" in missing.stderr
    assert copy.read_text() == MODULE.read_text()  # untouched on failure

    first = _run([], env, copy)
    assert first.returncode == 0 and "injected" in first.stdout
    assert "s3cret" not in first.stdout
    text = copy.read_text()
    assert "__PRESENCE_GOOGLE_CLIENT_ID__" not in text
    assert "__PRESENCE_GOOGLE_CLIENT_SECRET__" not in text
    assert 'CLIENT_ID = "1-abc.apps.googleusercontent.com"' in text
    assert 'CLIENT_SECRET = "s3cret-for-tests"' in text
    assert _run(["--check"], {}, copy).returncode == 0

    second = _run([], env, copy)
    assert second.returncode == 0 and "already in place" in second.stdout
    assert copy.read_text() == text

    mod = _load(copy)
    cfg = mod.builtin_client_config()
    assert cfg["installed"]["client_id"] == "1-abc.apps.googleusercontent.com"
    assert cfg["installed"]["client_secret"] == "s3cret-for-tests"
