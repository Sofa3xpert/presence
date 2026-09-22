"""The Models page: what is on this computer, what is loaded, room left — no network."""

import json

import fake_openai

from presence.app import modelcfg, ollama, server
from presence.app.config_io import read_secrets, read_yaml, write_secret, write_yaml
from presence.core import structured


class R:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status
        self.content = json.dumps(payload).encode()

    def json(self):
        return self.payload


def _row(name, size, **kw):
    row = ollama._model_row(name, size, kw.pop("digest", ""), kw.pop("modified", ""),
                            kw.pop("details", None))
    row.update(kw)
    return row


ENGINE = {"kind": "system", "version": "0.12.1", "port": 11434, "installed": False,
          "supported": True, "asset_size_mb": 40, "download": {}, "models_dir": "",
          "models_size_gb": 0.0}
FAKE_OV = {
    "running": True, "version": "0.12.1", "installed": True, "system": "Darwin",
    "download_url": "https://ollama.com/download/mac", "ram_gb": 16.0,
    "recommended": "nemotron-3-nano:4b", "recommended_size": "about 2.8 GB",
    "base_url": "http://127.0.0.1:11434", "engine": ENGINE,
    "models": [
        _row("nemotron-3-nano:4b", 3 * 2**30, digest="d2", modified="2026-09-01",
             details={"family": "nemotron", "parameter_size": "4B", "quantization_level": "Q4"},
             context=131072, capabilities=["completion"]),
        _row("qwen3.5:9b", 6 * 2**30, digest="d1", modified="2026-09-10",
             details={"family": "qwen3", "parameter_size": "9B", "quantization_level": "Q4_K_M"},
             loaded=True, memory=int(6.5 * 2**30), context=40960,
             capabilities=["completion", "tools"]),
    ],
    "models_dir": "/Users/x/.ollama/models", "models_size_gb": 9.0,
    "disk": {"total_gb": 460.0, "free_gb": 5.1}, "loaded_count": 1,
}
LOCAL_CFG = {"providers": {"local": {"kind": "openai-compatible",
                                     "base_url": "http://127.0.0.1:11434/v1"}},
             "agents": {"scout": {"provider": "local", "model": "qwen3.5:9b"}}}


def _client(tmp_path):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def test_list_loaded_and_show_parse_the_engine(monkeypatch):
    def fake_get(url, timeout):
        if url.endswith("/api/tags"):
            return R({"models": [
                {"name": "qwen3.5:9b", "size": 6_000_000_000, "digest": "d1",
                 "modified_at": "2026-09-10T12:00:00Z",
                 "details": {"family": "qwen3", "parameter_size": "9B",
                             "quantization_level": "Q4_K_M"}},
                {"name": "nemotron-3-nano:4b", "size": 3_000_000_000, "digest": "d2",
                 "modified_at": "2026-09-01T00:00:00Z", "details": {}}]})
        if url.endswith("/api/ps"):
            return R({"models": [{"name": "qwen3.5:9b", "size": 6_500_000_000,
                                  "size_vram": 6_400_000_000,
                                  "expires_at": "2026-09-17T23:59:00.123Z"}]})
        raise AssertionError(url)

    def fake_post(url, json, timeout):
        assert url.endswith("/api/show") and json == {"model": "qwen3.5:9b"}
        return R({"capabilities": ["completion", "tools"],
                  "model_info": {"qwen3.architecture": "qwen3", "qwen3.context_length": 40960}})

    monkeypatch.setattr(ollama.requests, "get", fake_get)
    monkeypatch.setattr(ollama.requests, "post", fake_post)
    ollama._shows.clear()
    rows = ollama.list_models("http://x")
    assert [r["name"] for r in rows] == ["nemotron-3-nano:4b", "qwen3.5:9b"]
    assert rows[1]["params"] == "9B" and rows[1]["quant"] == "Q4_K_M"
    assert rows[1]["modified"] == "2026-09-10" and rows[0]["family"] == ""
    assert ollama.loaded_models("http://x") == {
        "qwen3.5:9b": {"memory": 6_500_000_000, "vram": 6_400_000_000,
                       "expires": "2026-09-17T23:59:00"}}
    info = ollama.show_model("qwen3.5:9b", "d1", "http://x")
    assert info == {"context": 40960, "capabilities": ["completion", "tools"]}
    monkeypatch.setattr(ollama.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert ollama.show_model("qwen3.5:9b", "d1", "http://x") == info  # remembered per digest
    assert ollama.show_model("other", "", "http://x") == {}
    monkeypatch.setattr(ollama.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert ollama.list_models("http://x") == [] and ollama.loaded_models("http://x") == {}


def test_load_unload_delete_talk_to_the_engine(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(("post", url.rsplit("/", 1)[1], json))
        return R({"done": True})

    def fake_delete(url, json, timeout):
        calls.append(("delete", url.rsplit("/", 1)[1], json))
        return R({})

    monkeypatch.setattr(ollama.requests, "post", fake_post)
    monkeypatch.setattr(ollama.requests, "delete", fake_delete)
    assert ollama.load_model("qwen3.5:9b", "http://x") == (True, "ok")
    assert ollama.unload_model("qwen3.5:9b", "http://x") == (True, "ok")
    assert ollama.delete_model("qwen3.5:9b", "http://x") == (True, "ok")
    assert calls == [("post", "generate", {"model": "qwen3.5:9b", "keep_alive": -1}),
                     ("post", "generate", {"model": "qwen3.5:9b", "keep_alive": 0}),
                     ("delete", "delete", {"model": "qwen3.5:9b"})]
    monkeypatch.setattr(ollama.requests, "delete",
                        lambda url, json, timeout: R({"error": "model 'x' not found"}, 404))
    assert ollama.delete_model("x", "http://x") == (False, "model 'x' not found")
    monkeypatch.setattr(ollama.requests, "post", lambda url, json, timeout: R({}, 500))
    assert ollama.load_model("x", "http://x") == (False, "the engine answered 500")
    monkeypatch.setattr(ollama.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    ok, why = ollama.load_model("x", "http://x")
    assert not ok and why.startswith("OSError")


def test_overview_merges_live_state_and_falls_back_to_the_store(tmp_path, monkeypatch):
    store = tmp_path / "store"
    base = {**FAKE_OV, "models": ["qwen3.5:9b"]}
    monkeypatch.setattr(ollama, "status", lambda base_url=None: dict(base))
    monkeypatch.setattr(ollama, "models_dir", lambda info=None: store)
    monkeypatch.setattr(ollama, "list_models", lambda url: [
        _row("qwen3.5:9b", 6 * 2**30, digest="d1", details={"family": "qwen3"})])
    monkeypatch.setattr(ollama, "loaded_models",
                        lambda url: {"qwen3.5:9b": {"memory": 7 * 2**30, "vram": 0,
                                                    "expires": ""}})
    monkeypatch.setattr(ollama, "show_model",
                        lambda name, digest, url: {"context": 32768, "capabilities": ["tools"]})
    ov = ollama.overview()
    m = ov["models"][0]
    assert m["loaded"] and m["memory"] == 7 * 2**30 and m["context"] == 32768
    assert m["family"] == "qwen3" and ov["loaded_count"] == 1
    assert ov["models_size_gb"] == 6.0 and ov["models_dir"] == str(store)
    assert ov["disk"]["total_gb"] >= ov["disk"]["free_gb"] > 0  # the nearest existing parent
    # engine off: the store's manifests say what is installed, nothing is loaded
    lib = store / "manifests" / "registry.ollama.ai" / "library"
    (lib / "qwen3.5").mkdir(parents=True)
    (lib / "qwen3.5" / "9b").write_text(json.dumps(
        {"layers": [{"size": 5 * 2**30}, {"size": 2**20}]}))
    (store / "manifests" / "hf.co" / "someone" / "model").mkdir(parents=True)
    (store / "manifests" / "hf.co" / "someone" / "model" / "latest").write_text(
        json.dumps({"layers": [{"size": 1}]}))
    (lib / "broken").mkdir()
    (lib / "broken" / "x").write_text("nope")
    monkeypatch.setattr(ollama, "status", lambda base_url=None: {**base, "running": False})
    ov = ollama.overview()
    assert [(m["name"], m["size"]) for m in ov["models"]] == [
        ("hf.co/someone/model:latest", 1), ("qwen3.5:9b", 5 * 2**30 + 2**20)]
    assert not ov["models"][1]["loaded"] and ov["loaded_count"] == 0
    assert ov["models_size_gb"] == 5.0 and ov["models"][1]["modified"]


def test_models_dir_disk_usage_and_stop(tmp_path, monkeypatch):
    class Eng:
        models = tmp_path / "engine-models"

    monkeypatch.setattr(ollama, "engine", lambda: Eng())
    assert ollama.models_dir({"kind": "embedded", "installed": True}) == Eng.models
    assert ollama.models_dir({"kind": "none", "installed": True}) == Eng.models
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "mine"))
    assert ollama.models_dir({"kind": "system", "installed": False}) == tmp_path / "mine"
    monkeypatch.setattr(ollama, "engine", lambda: None)
    assert ollama.models_dir({"kind": "none", "installed": False}) == tmp_path / "mine"
    u = ollama.disk_usage(tmp_path / "not" / "there" / "yet")
    assert u["total_gb"] >= u["free_gb"] > 0
    # stop: only what Presence started, or its own engine
    monkeypatch.setattr(ollama, "engine_info", lambda: {"kind": "none", "installed": False})
    assert ollama.stop() == "the local engine is not running"

    class Served:
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    p = Served()
    monkeypatch.setattr(ollama, "_served", p)
    monkeypatch.setattr(ollama, "engine_info", lambda: {"kind": "system", "installed": False})
    assert "stopped" in ollama.stop() and p.terminated and ollama._served is None
    assert "outside Presence" in ollama.stop("Linux")

    class Own:
        stopped = False

        def stop(self):
            self.stopped = True

    own = Own()
    monkeypatch.setattr(ollama, "engine", lambda: own)
    monkeypatch.setattr(ollama, "engine_info", lambda: {"kind": "embedded", "installed": True})
    assert "stopped" in ollama.stop() and own.stopped


def test_models_page_shows_machine_models_and_actions(tmp_path, monkeypatch):
    c = _client(tmp_path)
    write_yaml(tmp_path / "presence.yaml", LOCAL_CFG)
    monkeypatch.setattr(ollama, "overview", lambda base_url=None: dict(FAKE_OV))
    monkeypatch.setattr(server, "model_ready", lambda *a, **k: True)
    page = c.get("/models").data.decode()
    assert "Models</a>" in page and 'href="/stories"' in page  # both new pages in the nav
    assert "qwen3.5:9b" in page and "in use" in page and ">ready<" in page
    assert "6.00 GB" in page and "on · 6.50 GB in memory" in page and "40k" in page
    assert "nemotron-3-nano:4b" in page and "off · on disk" in page and "128k" in page
    assert ">tools<" in page and "Turn off" in page and "Turn on" in page and "Remove" in page
    assert "running · 0.12.1" in page and "your Ollama" in page and "16.0 GB" in page
    assert "5.1 GB" in page and "of 460.0 GB" in page and "getting tight" in page
    assert "/Users/x/.ollama/models" in page and "1 model loaded now" in page
    assert "Stop the engine" in page and "Start the engine" not in page
    assert "Anthropic" in page and "no key" in page and "no address" in page
    gb = c.application.jinja_env.filters["gb"]
    assert gb(0) == "—" and gb(None) == "—" and gb(512 * 2**20) == "512 MB"
    assert gb(6 * 2**30) == "6.00 GB" and gb(12.34 * 2**30) == "12.3 GB"
    # engine off: what is on disk still shows, actions wait for the engine
    off = {**FAKE_OV, "running": False, "loaded_count": 0,
           "models": [_row("qwen3.5:9b", 6 * 2**30, modified="2026-09-10")]}
    monkeypatch.setattr(ollama, "overview", lambda base_url=None: off)
    page = c.get("/models").data.decode()
    assert ">stopped<" in page and "Start the engine" in page and "start the engine first" in page
    assert "Turn on" not in page and "Delete" not in page and "qwen3.5:9b" in page
    assert c.get("/models/status").get_json()["running"] is False


def test_models_use_switches_provider_and_model(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(ollama, "overview", lambda base_url=None: dict(FAKE_OV))
    monkeypatch.setattr(server, "model_ready", lambda *a, **k: True)
    monkeypatch.setattr(ollama, "resolve_base_url", lambda: "http://127.0.0.1:11434")
    r = c.post("/models/use", data={"kind": "local", "model": "qwen3.5:9b"},
               follow_redirects=True)
    assert b"is now your model" in r.data and r.request.path == "/models"
    cfg = read_yaml(tmp_path / "presence.yaml")
    assert cfg["agents"]["scout"] == {"provider": "local", "model": "qwen3.5:9b", "at": "08:00"}
    assert cfg["providers"]["local"]["base_url"] == "http://127.0.0.1:11434/v1"
    r = c.post("/models/use", data={"kind": "local", "model": ""}, follow_redirects=True)
    assert b"choose a model first" in r.data
    # a service needs its key saved first; the local entry is left alone
    r = c.post("/models/use", data={"kind": "anthropic", "model": "claude-opus-5"},
               follow_redirects=True)
    assert b"save an API key for Anthropic" in r.data
    write_secret(tmp_path, "ANTHROPIC_API_KEY", "sk-test")
    r = c.post("/models/use", data={"kind": "anthropic", "model": "claude-opus-5"},
               follow_redirects=True)
    assert b"model saved" in r.data and b"key saved" in r.data
    cur = modelcfg.current(tmp_path)
    assert cur["kind"] == "anthropic" and cur["model"] == "claude-opus-5"
    cfg = read_yaml(tmp_path / "presence.yaml")
    assert cfg["providers"]["local"]["base_url"] == "http://127.0.0.1:11434/v1"
    assert cfg["providers"]["anthropic"] == {"kind": "anthropic",
                                             "api_key_secret": "ANTHROPIC_API_KEY"}
    r = c.post("/models/use", data={"kind": "openai", "model": ""}, follow_redirects=True)
    assert b"save an API key for OpenAI" in r.data
    # the person's own server: an address first, then one of the models it offers
    r = c.post("/models/use", data={"kind": "endpoint", "model": "m1"}, follow_redirects=True)
    assert b"add your server" in r.data
    cfg["providers"]["endpoint"] = {"kind": "openai-compatible",
                                    "base_url": "http://10.0.0.2:8000/v1",
                                    "api_key_secret": "ENDPOINT_API_KEY"}
    write_yaml(tmp_path / "presence.yaml", cfg)
    monkeypatch.setattr(server.probe, "list_models", lambda *a, **k: ["m1", "m2"])
    page = c.get("/models").data.decode()
    assert ">m1<" in page and ">m2<" in page and ">answering<" in page
    r = c.post("/models/use", data={"kind": "endpoint", "model": ""}, follow_redirects=True)
    assert b"pick one of the models" in r.data
    r = c.post("/models/use", data={"kind": "endpoint", "model": "m2"}, follow_redirects=True)
    assert b"model saved" in r.data and modelcfg.current(tmp_path)["model"] == "m2"
    r = c.post("/models/use", data={"kind": "nope"}, follow_redirects=True)
    assert b"choose a provider" in r.data


def test_models_on_off_remove_and_engine_routes(tmp_path, monkeypatch):
    c = _client(tmp_path)
    write_yaml(tmp_path / "presence.yaml", LOCAL_CFG)
    monkeypatch.setattr(ollama, "overview", lambda base_url=None: dict(FAKE_OV))
    monkeypatch.setattr(server, "model_ready", lambda *a, **k: True)
    seen = []
    for name, verb in (("load_model", "load"), ("unload_model", "unload"),
                       ("delete_model", "delete")):
        monkeypatch.setattr(ollama, name,
                            lambda m, _v=verb: (seen.append((_v, m)), (True, "ok"))[1])
    r = c.post("/models/load", data={"model": "qwen3.5:9b"}, follow_redirects=True)
    assert b"qwen3.5:9b is on" in r.data and r.request.path == "/models"
    r = c.post("/models/unload", data={"model": "qwen3.5:9b"}, follow_redirects=True)
    assert b"qwen3.5:9b is off" in r.data
    r = c.post("/models/remove", data={"model": "qwen3.5:9b"}, follow_redirects=True)
    assert b"removed from this computer" in r.data and b"pick another" in r.data
    r = c.post("/models/remove", data={"model": "nemotron-3-nano:4b"}, follow_redirects=True)
    assert b"removed from this computer" in r.data and b"pick another" not in r.data
    assert seen == [("load", "qwen3.5:9b"), ("unload", "qwen3.5:9b"),
                    ("delete", "qwen3.5:9b"), ("delete", "nemotron-3-nano:4b")]
    monkeypatch.setattr(ollama, "load_model", lambda m: (False, "model not found"))
    r = c.post("/models/load", data={"model": "ghost"}, follow_redirects=True)
    assert b"ghost: model not found" in r.data
    monkeypatch.setattr(ollama, "stop", lambda: "the local engine is stopped")
    r = c.post("/ollama/stop", follow_redirects=True)
    assert b"is stopped" in r.data and r.request.path == "/models"
    # the engine's start button goes back to whichever page pressed it
    monkeypatch.setattr(ollama, "start", lambda: "starting `ollama serve`")
    r = c.post("/ollama/start", data={"back": "models"}, follow_redirects=True)
    assert r.request.path == "/models" and b"starting" in r.data
    r = c.post("/ollama/start", follow_redirects=True)
    assert r.request.path == "/"
    monkeypatch.setattr(ollama, "readiness",
                        lambda model, base_url=None: (True, "tool call worked · 40 tokens"))
    r = c.post("/ollama/check", data={"model": "qwen3.5:9b", "back": "models"},
               follow_redirects=True)
    assert r.request.path == "/models" and b"is ready" in r.data


def test_nim_preset_saved_from_setup(tmp_path):
    """Setup step 3 with the NIM provider: no address means a container on this
    computer, and NVIDIA's own endpoint refuses to be saved without a key."""
    app = server.create_app(tmp_path)
    c = app.test_client()

    model = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    c.post("/setup/model", data={"kind": "nim", "model": model}, follow_redirects=True)
    cfg = read_yaml(tmp_path / "presence.yaml")
    assert cfg["providers"]["nim"]["base_url"] == modelcfg.NIM_LOCAL
    assert cfg["providers"]["nim"]["api_key_secret"] == "NVIDIA_API_KEY"
    assert cfg["agents"]["scout"]["provider"] == "nim"

    page = c.post("/setup/model",
                  data={"kind": "nim", "model": "x", "base_url": modelcfg.NIM_HOSTED},
                  follow_redirects=True).get_data(as_text=True)
    assert "build.nvidia.com" in page
    saved = read_yaml(tmp_path / "presence.yaml")["providers"]["nim"]
    assert saved["base_url"] == modelcfg.NIM_LOCAL

    c.post("/setup/model", data={"kind": "nim", "model": "m", "base_url": modelcfg.NIM_HOSTED,
                                 "api_key": "nvapi-test"}, follow_redirects=True)
    cfg = read_yaml(tmp_path / "presence.yaml")
    assert cfg["providers"]["nim"]["base_url"] == modelcfg.NIM_HOSTED
    assert read_secrets(tmp_path)["NVIDIA_API_KEY"] == "nvapi-test"


def test_nim_reaches_the_transport_as_an_endpoint(tmp_path, monkeypatch):
    """A saved NIM provider is an OpenAI-compatible endpoint: nested json_schema,
    the answer-directly switches, and the address the person gave."""
    app = server.create_app(tmp_path)
    app.test_client().post(
        "/setup/model",
        data={"kind": "nim", "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
              "base_url": modelcfg.NIM_HOSTED, "api_key": "nvapi-test"},
        follow_redirects=True)
    cur = modelcfg.current(tmp_path)
    assert cur["kind"] == "endpoint" and cur["api_key"] == "nvapi-test"

    fake = fake_openai.install(monkeypatch, fake_openai.reply('{"ok": true}'))
    out = structured.ask_json(cur["kind"], cur["model"], "s", "p",
                              {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                              base_url=cur["base_url"], api_key=cur["api_key"])
    assert out == {"ok": True}
    assert fake["base_url"] == modelcfg.NIM_HOSTED
    assert fake["api_key"] == "nvapi-test"
    call = fake["calls"][-1]
    assert call["response_format"]["type"] == "json_schema"
    assert "schema" in call["response_format"]["json_schema"]
    assert call["extra_body"] == structured.THINK_OFF
