"""Unit tests for the pure helpers of the macOS launcher (packaging/macos/launcher.py).

No rumps, no Flask server, no network: only the functions that decide ports,
paths and logging.
"""

import importlib.util
import io
import os
import socket
import sys
import urllib.error
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "packaging" / "macos" / "launcher.py"


@pytest.fixture(scope="module")
def launcher():
    spec = importlib.util.spec_from_file_location("presence_mac_launcher", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_import_has_no_side_effects(launcher):
    assert "rumps" not in sys.modules
    assert launcher.PREFERRED_PORT == 8790
    assert launcher.HOST == "127.0.0.1"


def test_log_path_under_library_logs(launcher, tmp_path):
    assert launcher.log_path(tmp_path) == tmp_path / "Library" / "Logs" / "Presence.log"


def test_redirect_stdio_tees_to_log_and_console(launcher, tmp_path, monkeypatch):
    console = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", console)
    monkeypatch.setattr(sys, "__stderr__", console)
    saved = sys.stdout, sys.stderr
    log = tmp_path / "Library" / "Logs" / "Presence.log"
    try:
        handle = launcher.redirect_stdio(log)
        print("hello log")
        sys.stderr.write("warn\n")
        sys.stdout.flush()
        assert not sys.stdout.isatty()
    finally:
        sys.stdout, sys.stderr = saved
        handle.close()
    assert log.read_text() == "hello log\nwarn\n"
    assert console.getvalue() == "hello log\nwarn\n"


def test_redirect_stdio_without_console(launcher, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "__stdout__", None)
    monkeypatch.setattr(sys, "__stderr__", None)
    saved = sys.stdout, sys.stderr
    log = tmp_path / "Presence.log"
    try:
        handle = launcher.redirect_stdio(log)
        print("only the log")
    finally:
        sys.stdout, sys.stderr = saved
        handle.close()
    assert log.read_text() == "only the log\n"


def test_gui_path_prepends_missing_bins(launcher):
    sep = os.pathsep
    out = launcher.gui_path(sep.join(["/usr/bin", "/bin"]))
    assert out.split(sep) == ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    again = launcher.gui_path(out)
    assert again == out
    assert launcher.gui_path(None).split(sep) == ["/opt/homebrew/bin", "/usr/local/bin"]


def test_pick_port_prefers_free_port(launcher):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert launcher.pick_port(free) == free


def test_pick_port_falls_back_when_taken(launcher):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        busy = taken.getsockname()[1]
        assert launcher.port_is_free(busy) is False
        chosen = launcher.pick_port(busy)
    assert chosen != busy
    assert 1024 < chosen < 65536


class _Resp:
    def __init__(self, status):
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_presence_answers_on_health_or_root(launcher):
    calls = []

    def opener(url, timeout):
        calls.append(url)
        if url.endswith("/health"):
            raise urllib.error.HTTPError(url, 404, "nope", {}, None)
        return _Resp(200)

    assert launcher.presence_answers(8790, opener=opener) is True
    assert calls == ["http://127.0.0.1:8790/health", "http://127.0.0.1:8790/"]


def test_presence_answers_false_when_nothing_listens(launcher):
    def opener(url, timeout):
        raise urllib.error.URLError("connection refused")

    assert launcher.presence_answers(8790, opener=opener) is False

    def server_error(url, timeout):
        return _Resp(500)

    assert launcher.presence_answers(8790, opener=server_error) is False


def test_needs_init_checks_presence_yaml(launcher, tmp_path):
    assert launcher.needs_init(tmp_path) is True
    (tmp_path / "presence.yaml").write_text("provider: ollama\n")
    assert launcher.needs_init(tmp_path) is False


def test_url_and_resource_path(launcher):
    assert launcher.url_for(8791) == "http://127.0.0.1:8791/"
    assert launcher.resource_path("menubar.png") == str(LAUNCHER.parent / "menubar.png")
    assert (LAUNCHER.parent / "menubar.png").exists()
