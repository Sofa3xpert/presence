"""Embedded local engine: release lookup, download + checksum, unpack, port
choice, pidfile handling, install flow, status — all without the network."""

import hashlib
import io
import json
import os
import tarfile
import time

import pytest
from conftest import needs_loopback

from presence.app import ollama, ollama_embedded, server
from presence.app.ollama_embedded import Engine

FAKE_BIN = b"#!/bin/sh\necho fake\n"


def make_tgz(path, extra_files=None):
    with tarfile.open(path, "w:gz") as t:
        info = tarfile.TarInfo("ollama")
        info.size = len(FAKE_BIN)
        info.mode = 0o755
        t.addfile(info, io.BytesIO(FAKE_BIN))
        for name, body in (extra_files or {}).items():
            i = tarfile.TarInfo(name)
            i.size = len(body)
            t.addfile(i, io.BytesIO(body))
    return path.read_bytes()


class FakeResponse:
    def __init__(self, body=b"", status=200, headers=None, payload=None):
        self.content = body
        self.status_code = status
        self.headers = headers or {}
        self.text = body.decode(errors="replace") if isinstance(body, bytes) else body
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError(f"http {self.status_code}")

    def iter_content(self, n):
        for i in range(0, len(self.content), n):
            yield self.content[i : i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def darwin(monkeypatch):
    monkeypatch.setattr(ollama_embedded.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ollama_embedded.platform, "machine", lambda: "arm64")


@pytest.fixture
def release(tmp_path, monkeypatch, darwin):
    """A fake GitHub: releases/latest, the archive (with Range), sha256sum.txt."""
    archive = make_tgz(tmp_path / "src.tgz", {"LLAMA_LICENSE": b"MIT"})
    digest = hashlib.sha256(archive).hexdigest()
    calls = []

    def fake_get(url, **kw):
        calls.append((url, kw.get("headers") or {}))
        if url == ollama_embedded.RELEASES_LATEST:
            return FakeResponse(payload={"tag_name": "v0.34.0"})
        if url.endswith("/sha256sum.txt"):
            return FakeResponse(f"{digest}  ./ollama-darwin.tgz\nabc  ./other\n".encode())
        if url.endswith("/ollama-darwin.tgz"):
            rng = (kw.get("headers") or {}).get("Range")
            if rng:
                start = int(rng.split("=")[1].rstrip("-"))
                return FakeResponse(
                    archive[start:], 206, {"Content-Length": str(len(archive) - start)}
                )
            return FakeResponse(archive, 200, {"Content-Length": str(len(archive))})
        if url.endswith("/api/version"):
            raise OSError("nothing listening")
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(ollama_embedded.requests, "get", fake_get)
    return {"archive": archive, "digest": digest, "calls": calls}


def test_asset_by_platform_and_checksum_parsing():
    assert ollama_embedded.asset_for("Darwin", "arm64") == ("ollama-darwin.tgz", 159)
    assert ollama_embedded.asset_for("Windows", "AMD64")[0] == "ollama-windows-amd64.zip"
    assert ollama_embedded.asset_for("Linux", "aarch64")[0].endswith(".tar.zst")
    assert ollama_embedded.asset_for("Plan9", "mips") is None
    text = "a" * 64 + "  ./ollama-darwin.tgz\n" + "b" * 64 + " ollama-windows-amd64.zip\njunk\n"
    assert ollama_embedded.parse_checksums(text) == {
        "ollama-darwin.tgz": "a" * 64,
        "ollama-windows-amd64.zip": "b" * 64,
    }
    assert ollama_embedded.version_tuple("v0.34.0") == (0, 34, 0)
    assert ollama_embedded.version_tuple("0.34.0-rc1") == (0, 34, 0)
    assert ollama_embedded.version_tuple("weird") == (0,)


def test_resolve_version_caches_for_a_day(tmp_path, release):
    eng = Engine(tmp_path)
    assert eng.resolve_version() == "0.34.0"
    assert eng.resolve_version() == "0.34.0"
    lookups = [c for c in release["calls"] if c[0] == ollama_embedded.RELEASES_LATEST]
    assert len(lookups) == 1
    cache = json.loads(eng.version_cache.read_text())
    assert cache["version"] == "0.34.0" and cache["asset"] == "ollama-darwin.tgz"
    cache["checked_at"] = time.time() - 2 * ollama_embedded.VERSION_CACHE_SECONDS
    eng.version_cache.write_text(json.dumps(cache))
    eng.resolve_version()
    assert len([c for c in release["calls"] if c[0] == ollama_embedded.RELEASES_LATEST]) == 2


def test_download_verify_unpack_and_resume(tmp_path, release):
    eng = Engine(tmp_path)
    state = ollama_embedded._new_state()
    # a half-finished earlier download is resumed with a Range header
    eng.versions.mkdir(parents=True)
    part = eng.versions / "ollama-darwin.tgz.part"
    part.write_bytes(release["archive"][:10])
    archive = eng.download("0.34.0", "ollama-darwin.tgz", state)
    assert archive.read_bytes() == release["archive"]
    assert state["bytes_done"] == state["bytes_total"] == len(release["archive"])
    assert any(h.get("Range") == "bytes=10-" for _u, h in release["calls"])
    assert eng.verify("0.34.0", "ollama-darwin.tgz", archive) == release["digest"]
    dest = eng.unpack(archive, eng.version_dir("0.34.0"))
    assert (dest / "ollama").read_bytes() == FAKE_BIN
    assert os.access(dest / "ollama", os.X_OK)
    assert (dest / "LLAMA_LICENSE").read_text() == "MIT"
    eng.set_current("0.34.0")
    assert eng.current_version() == "0.34.0"
    assert eng.binary() == dest / "ollama"
    assert eng.installed_versions() == ["0.34.0"]


def test_bad_checksum_is_refused(tmp_path, release):
    eng = Engine(tmp_path)
    eng.versions.mkdir(parents=True)
    bad = eng.versions / "ollama-darwin.tgz.part"
    bad.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="checksum"):
        eng.verify("0.34.0", "ollama-darwin.tgz", bad)
    assert not bad.exists()


def test_port_choice_keeps_saved_port_when_free(tmp_path, darwin):
    eng = Engine(tmp_path)
    p = ollama_embedded.free_port()
    assert 1024 < p < 65536 and ollama_embedded.port_is_free(p)
    eng.root.mkdir(parents=True)
    eng.portfile.write_text(str(p))
    assert eng.pick_port() == p
    eng.portfile.write_text(str(ollama_embedded.SYSTEM_PORT))  # never race the person's app
    assert eng.pick_port() != ollama_embedded.SYSTEM_PORT
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", p))
    s.listen(1)
    try:
        eng.portfile.write_text(str(p))
        assert eng.pick_port() != p
    finally:
        s.close()


def test_pidfile_handling(tmp_path, darwin, monkeypatch):
    eng = Engine(tmp_path)
    eng.root.mkdir(parents=True)
    assert eng.stale_pid() is None and eng.kill_stale() is False
    eng.pidfile.write_text("not a pid")
    assert eng.kill_stale() is False and not eng.pidfile.exists()
    # a live pid that is NOT our engine is left alone
    eng.pidfile.write_text(str(os.getpid()))
    monkeypatch.setattr(ollama_embedded, "_cmdline", lambda pid: "/Applications/Ollama.app/ollama")
    killed = []
    monkeypatch.setattr(ollama_embedded, "_terminate", lambda pid, wait=10: killed.append(pid))
    assert eng.kill_stale() is False and killed == [] and not eng.pidfile.exists()
    # a live pid running our engine dir is terminated
    eng.pidfile.write_text(str(os.getpid()))
    ours = f"{eng.versions}/0.34.0/ollama serve"
    monkeypatch.setattr(ollama_embedded, "_cmdline", lambda pid: ours)
    assert eng.kill_stale() is True and killed == [os.getpid()] and not eng.pidfile.exists()
    # a dead pid is just cleaned up
    eng.pidfile.write_text("999999999")
    assert eng.running_port() is None
    assert eng.kill_stale() is False


def test_install_flow_end_to_end_with_fake_launch(tmp_path, release, monkeypatch):
    eng = Engine(tmp_path)
    monkeypatch.setattr(Engine, "launch", lambda self, timeout=30: 43210)
    state = eng.install()
    assert state["phase"] == "ready" and state["port"] == 43210 and state["version"] == "0.34.0"
    assert eng.installed() and eng.current_version() == "0.34.0"
    assert not (eng.versions / "ollama-darwin.tgz.part").exists()
    assert not (eng.versions / "0.34.0.tmp").exists()
    # up to date → no update
    assert eng.check_update()["updated"] is False


def test_upgrade_swaps_pointer_and_keeps_one_previous(tmp_path, release, monkeypatch):
    eng = Engine(tmp_path)
    monkeypatch.setattr(Engine, "launch", lambda self, timeout=30: 43210)
    for v in ("0.32.0", "0.33.0"):
        d = eng.version_dir(v)
        d.mkdir(parents=True)
        (d / "ollama").write_bytes(FAKE_BIN)
    eng.set_current("0.33.0")
    result = eng.check_update()
    assert result == {"updated": True, "current": "0.34.0", "previous": "0.33.0",
                      "latest": "0.34.0"}
    assert eng.current_version() == "0.34.0"
    assert eng.installed_versions() == ["0.33.0", "0.34.0"]


@needs_loopback
@pytest.mark.skipif(os.name == "nt", reason="the stand-in engine is a shell script")
def test_launch_and_stop_a_real_child(tmp_path, darwin, monkeypatch):
    """A tiny stand-in 'engine' (python http server) proves the child lifecycle:
    pidfile + portfile written, health poll, adopted after a restart, stopped."""
    import sys

    eng_dir = tmp_path / "engine" / "ollama" / "0.34.0"
    eng_dir.mkdir(parents=True)
    (eng_dir / "fake_server.py").write_text(
        "import os, http.server as h\n"
        "port = int(os.environ['OLLAMA_HOST'].split(':')[1])\n"
        "class H(h.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.end_headers()\n"
        "        self.wfile.write(b'{\"version\": \"fake\"}')\n"
        "    def log_message(self, *a): pass\n"
        "h.HTTPServer(('127.0.0.1', port), H).serve_forever()\n"
    )
    fake = eng_dir / "ollama"
    fake.write_text('#!/bin/sh\nexec "$PYTHON" "$(dirname "$0")/fake_server.py"\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PYTHON", sys.executable)
    eng = Engine(tmp_path)
    eng.set_current("0.34.0")
    monkeypatch.setattr(ollama_embedded, "_install_signal_handler", lambda e: None)
    port = eng.launch(timeout=15)
    assert int(eng.portfile.read_text()) == port and eng.pidfile.exists()
    assert eng.running_port() == port
    assert eng.launch() == port  # already up: same port, no second child
    assert eng.info()["kind"] == "embedded" and eng.info()["port"] == port
    assert eng.base_url() == f"http://127.0.0.1:{port}"
    assert (tmp_path / "logs" / "ollama.log").exists()
    # a fresh Engine (app restarted) adopts the child through the pidfile
    again = Engine(tmp_path)
    assert again.running_port() == port
    again.stop()
    assert not again.pidfile.exists()
    assert eng.running_port() is None
    assert eng.proc.poll() is not None


def test_status_and_engine_routes(tmp_path, monkeypatch, darwin):
    monkeypatch.setattr(ollama_embedded, "api_version", lambda port, timeout=2: None)
    monkeypatch.setattr(ollama, "ram_gb", lambda: 16.0)
    monkeypatch.setattr(ollama, "installed_binary", lambda sysname=None: None)
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    c = app.test_client()
    j = c.get("/ollama/status").get_json()
    assert j["running"] is False and j["recommended"] == "qwen3.5:9b"
    assert j["engine"]["kind"] == "none" and j["engine"]["installed"] is False
    assert j["engine"]["asset_size_mb"] == 159 and j["engine"]["supported"] is True
    assert j["engine"]["models_dir"] == str(tmp_path / "models")
    assert j["engine"]["download"]["phase"] == "idle"
    assert j["base_url"] == ollama.DEFAULT_URL
    e = c.get("/ollama/engine/status").get_json()
    assert set(e) >= {"kind", "version", "port", "download", "installed", "models_size_gb"}
    started = []
    monkeypatch.setattr(Engine, "install_in_background", lambda self: started.append(1) or
                        {**self.state, "phase": "resolving"})
    assert c.post("/ollama/engine/install").get_json()["phase"] == "resolving" and started
    # the person's own Ollama on 11434 → kind "system", their port
    monkeypatch.setattr(
        ollama_embedded, "api_version",
        lambda port, timeout=2: "0.12.1" if port == ollama_embedded.SYSTEM_PORT else None,
    )
    e = c.get("/ollama/engine/status").get_json()
    assert e["kind"] == "system" and e["port"] == 11434 and e["version"] == "0.12.1"


def test_start_route_prefers_embedded_and_resyncs_saved_url(tmp_path, monkeypatch, darwin):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    c = app.test_client()
    eng = ollama.engine()
    monkeypatch.setattr(Engine, "installed", lambda self: True)
    monkeypatch.setattr(Engine, "launch", lambda self, timeout=30: 45678)
    monkeypatch.setattr(Engine, "running_port", lambda self: 45678)
    monkeypatch.setattr(ollama, "readiness", lambda model, base_url=None: (True, "ok · 9 tokens"))
    from presence.app.config_io import read_yaml

    c.post("/ollama/check", data={"model": "qwen3.5:4b"}, follow_redirects=True)
    cfg = read_yaml(tmp_path / "presence.yaml")
    assert cfg["providers"]["local"]["base_url"] == "http://127.0.0.1:45678/v1"
    assert cfg["agents"]["scout"]["model"] == "qwen3.5:4b"
    monkeypatch.setattr(Engine, "running_port", lambda self: 45679)
    j = c.post("/ollama/start?json=1").get_json()
    assert "running on port 45678" in j["message"]
    cfg = read_yaml(tmp_path / "presence.yaml")
    assert cfg["providers"]["local"]["base_url"] == "http://127.0.0.1:45679/v1"
    assert cfg["agents"]["scout"]["model"] == "qwen3.5:4b"  # untouched by a resync
    assert eng is ollama.engine()
