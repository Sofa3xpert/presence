"""Starting the app like a person does: ports, one instance, the browser,
and the background daily run — all on loopback, no network."""

import asyncio
import json
import socket
import time
from datetime import datetime

import requests
from conftest import needs_loopback

from presence.app import run
from presence.app.config_io import write_yaml

PROFILE = {"identity": {"name": "Ada"}, "work_authorization": {"summary": "full right"},
           "confirmed": True}
SOURCES = {"sources": [{"id": "acme", "provider": "greenhouse", "label": "Acme",
                        "enabled": True, "config": {"board": "acme"}}]}


def _seed(data, confirmed=True, enabled=True, at="08:00"):
    write_yaml(data / "profile.yaml", {**PROFILE, "confirmed": confirmed})
    src = {"sources": [{**SOURCES["sources"][0], "enabled": enabled}]}
    write_yaml(data / "sources.yaml", src)
    write_yaml(data / "search.yaml", {"locations": []})
    write_yaml(data / "presence.yaml", {
        "providers": {"local": {"kind": "openai-compatible", "base_url": "http://localhost:1/v1"}},
        "agents": {"scout": {"provider": "local", "model": "m", "at": at},
                   "brief": {"provider": "local", "model": "m", "at": "09:00"}}})


def test_ready_gates_on_profile_and_boards(tmp_path):
    _seed(tmp_path, confirmed=False)
    ok, why = run.ready(tmp_path)
    assert not ok and "Setup, step 4" in why
    _seed(tmp_path, enabled=False)
    ok, why = run.ready(tmp_path)
    assert not ok and "Setup, step 5" in why
    _seed(tmp_path)
    assert run.ready(tmp_path) == (True, "")
    assert run.scout_at(tmp_path) == "08:00"
    _seed(tmp_path, at="7:5")
    assert run.scout_at(tmp_path) == "07:05"
    (tmp_path / "presence.yaml").write_text("providers: [")
    assert run.scout_at(tmp_path) == run.DEFAULT_AT


def test_tick_skips_until_ready_then_runs_and_never_raises(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(run, "run_cycle",
                        lambda data, send=False: ran.append(send) or ("b", {}, False))
    clock = lambda: datetime.fromisoformat("2026-09-14T10:00:00")  # noqa: E731
    scheduler = run.build_scheduler(tmp_path, clock=clock)
    _seed(tmp_path, confirmed=False)
    assert asyncio.run(run.tick(tmp_path, scheduler)) == []
    assert ran == [] and not (tmp_path / "scheduler.json").exists()
    _seed(tmp_path)
    assert asyncio.run(run.tick(tmp_path, scheduler)) == ["cycle"]
    assert ran == [True]  # the automatic run sends to Telegram when paired
    state = json.loads((tmp_path / "scheduler.json").read_text())
    assert state["2026-09-14"]["cycle"]["status"] == "ok"
    assert asyncio.run(run.tick(tmp_path, scheduler)) == []  # once a day
    # a failing cycle is recorded, not raised
    def boom(data, send=False):
        raise RuntimeError("boom")
    monkeypatch.setattr(run, "run_cycle", boom)
    (tmp_path / "scheduler.json").unlink()
    assert asyncio.run(run.tick(tmp_path, scheduler)) == ["cycle"]
    state = json.loads((tmp_path / "scheduler.json").read_text())
    assert state["2026-09-14"]["cycle"]["status"] == "error"
    # even a broken scheduler state file cannot raise out of a tick
    (tmp_path / "scheduler.json").write_text("{not json")
    assert asyncio.run(run.tick(tmp_path, scheduler)) == []


def test_next_run_wording(tmp_path, monkeypatch):
    _seed(tmp_path, confirmed=False)
    assert "while Presence is open" in run.next_run(tmp_path)
    monkeypatch.setattr(run, "scheduler_running", lambda data: True)
    assert run.next_run(tmp_path).startswith("as soon as setup is complete")
    _seed(tmp_path)
    now = datetime.fromisoformat("2026-09-14T07:00:00")
    assert run.next_run(tmp_path, now) == "today at 08:00"
    now = datetime.fromisoformat("2026-09-14T10:00:00")
    assert run.next_run(tmp_path, now).startswith("in a moment")
    (tmp_path / "scheduler.json").write_text(json.dumps(
        {"2026-09-14": {"cycle": {"status": "ok", "attempts": 1}}}))
    assert run.next_run(tmp_path, now).startswith("tomorrow at 08:00")
    (tmp_path / "scheduler.json").write_text(json.dumps(
        {"2026-09-14": {"cycle": {"status": "error", "attempts": 3}}}))
    assert "did not succeed" in run.next_run(tmp_path, now)


def test_pick_port_skips_busy_ports():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy = s.getsockname()[1]
        assert not run.port_free(busy)
        assert run.pick_port(busy) != busy  # falls back to a free one
    assert run.port_free(run.pick_port(None))


@needs_loopback
def test_serve_answers_health_opens_browser_once_and_reuses_instance(tmp_path, monkeypatch):
    _seed(tmp_path, confirmed=False)
    opened = []
    monkeypatch.setattr(run.webbrowser, "open", lambda url: opened.append(url) or True)
    started = []
    monkeypatch.setattr(run, "start_scheduler", lambda data, interval=30.0: started.append(data))
    port = run.pick_port(None)
    url = run.serve(tmp_path, port=port, open_browser=True, block=False)
    assert url == f"http://127.0.0.1:{port}" and run.server_thread(url) is not None
    body = requests.get(f"{url}/health", timeout=2).json()
    assert body["app"] == "presence" and body["version"]
    assert started == [tmp_path]
    deadline = 50
    while not opened and deadline:
        time.sleep(0.1)
        deadline -= 1
    assert opened == [url]
    # a second serve on the same port finds the running one and only opens the browser
    assert run.serve(tmp_path, port=port, open_browser=True, block=False) == url
    assert opened == [url, url] and started == [tmp_path]
    assert (tmp_path / "logs" / "presence.log").exists()
