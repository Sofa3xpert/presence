"""Your own server as a model kind: settings, routes, the page — no network."""

import pytest

from presence.app import modelcfg, ollama, server
from presence.app.config_io import read_secrets, read_yaml, write_secret, write_yaml
from presence.core import check, probe

OWN = "http://localhost:8000/v1"


@pytest.fixture
def client(tmp_path):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client(), tmp_path


def test_derive_kind_reads_the_provider_entry_not_the_address():
    assert modelcfg.derive_kind("local", {}) == "local"
    assert modelcfg.derive_kind("local", {"base_url": "http://localhost:11434/v1"}) == "local"
    assert modelcfg.derive_kind("local", {"base_url": "http://127.0.0.1:11434"}) == "local"
    assert modelcfg.derive_kind("local", {"base_url": "http://localhost:1234/v1"}) == "endpoint"
    assert modelcfg.derive_kind("endpoint", {"base_url": OWN}) == "endpoint"
    assert modelcfg.derive_kind("mine", {"kind": "openai-compatible",
                                         "base_url": "https://nas.local/v1"}) == "endpoint"
    assert modelcfg.derive_kind("openai", {"api_key_secret": "OPENAI_API_KEY"}) == "openai"
    assert modelcfg.derive_kind("openai", {"base_url": "https://proxy.example/v1"}) == "openai"
    assert modelcfg.derive_kind("mine", {"kind": "openai-compatible"}) == "openai"
    assert modelcfg.derive_kind("anthropic", {}) == "anthropic"
    assert modelcfg.derive_kind("mine", {"kind": "anthropic"}) == "anthropic"


def test_current_passes_the_own_server_address_through_verbatim(tmp_path):
    write_yaml(tmp_path / "presence.yaml", {
        "providers": {"endpoint": {"kind": "openai-compatible", "base_url": OWN,
                                   "api_key_secret": "ENDPOINT_API_KEY"}},
        "agents": {"scout": {"provider": "endpoint", "model": "qwen3"}},
    })
    cfg = modelcfg.current(tmp_path)
    assert cfg == {"kind": "endpoint", "model": "qwen3", "base_url": OWN, "api_key": "unused"}
    write_secret(tmp_path, "ENDPOINT_API_KEY", "tok")
    assert modelcfg.current(tmp_path)["api_key"] == "tok"
    # a local provider that was pointed at LM Studio the old way keeps that address too
    write_yaml(tmp_path / "presence.yaml", {
        "providers": {"local": {"kind": "openai-compatible",
                                "base_url": "http://localhost:1234/v1"}},
        "agents": {"scout": {"provider": "local", "model": "m"}},
    })
    assert modelcfg.current(tmp_path)["base_url"] == "http://localhost:1234/v1"
    # while the engine's own address is resolved live
    write_yaml(tmp_path / "presence.yaml", {
        "providers": {"local": {"kind": "openai-compatible",
                                "base_url": "http://localhost:11434/v1"}},
        "agents": {"scout": {"provider": "local", "model": "m"}},
    })
    assert modelcfg.current(tmp_path) == {"kind": "local", "model": "m",
                                          "base_url": ollama.resolve_base_url(),
                                          "api_key": ""}


def test_setup_model_saves_an_own_server_and_keeps_the_rest(client):
    c, data = client
    c.post("/setup/model", data={"kind": "anthropic", "api_key": "sk-a", "model": "claude"})
    cfg = read_yaml(data / "presence.yaml")
    cfg["agents"]["writer"] = {"provider": "anthropic", "model": "claude", "at": "10:00"}
    cfg["agents"]["scout"]["max_turns"] = 3
    write_yaml(data / "presence.yaml", cfg)
    r = c.post("/setup/model", data={"kind": "endpoint", "base_url": OWN + "/", "model": "qwen3",
                                     "api_key": "tok"}, follow_redirects=True)
    assert b"model saved \xe2\x80\x94 your own server, qwen3" in r.data
    cfg = read_yaml(data / "presence.yaml")
    assert cfg["providers"]["endpoint"] == {"kind": "openai-compatible", "base_url": OWN,
                                            "api_key_secret": "ENDPOINT_API_KEY"}
    assert cfg["providers"]["anthropic"]["api_key_secret"] == "ANTHROPIC_API_KEY"  # kept
    assert cfg["agents"]["scout"] == {"provider": "endpoint", "model": "qwen3", "at": "08:00",
                                      "max_turns": 3}
    assert cfg["agents"]["brief"]["provider"] == "endpoint"
    assert cfg["agents"]["writer"] == {"provider": "anthropic", "model": "claude", "at": "10:00"}
    assert read_secrets(data)["ENDPOINT_API_KEY"] == "tok"
    assert modelcfg.current(data)["base_url"] == OWN
    r = c.post("/setup/model", data={"kind": "endpoint", "model": "m"}, follow_redirects=True)
    assert b"type your server" in r.data
    r = c.post("/setup/model", data={"kind": "endpoint", "base_url": OWN}, follow_redirects=True)
    assert b"name of the model" in r.data
    r = c.post("/setup/model", data={"kind": "weird", "model": "m"}, follow_redirects=True)
    assert r.status_code == 200 and b"choose a provider" in r.data


def test_model_ready_for_an_own_server_asks_its_model_list(client, monkeypatch):
    c, data = client
    c.post("/setup/model", data={"kind": "endpoint", "base_url": OWN, "model": "qwen3"})
    asked = []
    monkeypatch.setattr(probe, "list_models",
                        lambda base, key="unused", timeout=4: asked.append((base, key)) or None)
    server._model_cache["key"] = None
    st = server._state(data)
    assert st["model"] == {"kind": "endpoint", "model": "qwen3", "base_url": OWN,
                           "has_key": False}
    assert not st["model_done"] and asked == [(OWN, "unused")]
    monkeypatch.setattr(probe, "list_models", lambda base, key="unused", timeout=4: ["qwen3"])
    assert not server._state(data)["model_done"]  # cached for a few seconds
    server._model_cache["at"] = 0
    assert server._state(data)["model_done"]
    write_secret(data, "ENDPOINT_API_KEY", "tok")
    assert server._state(data)["model"]["has_key"]


def test_engine_resync_touches_only_the_local_provider(client, monkeypatch):
    c, data = client
    write_yaml(data / "presence.yaml", {
        "providers": {
            "endpoint": {"kind": "openai-compatible", "base_url": OWN,
                         "api_key_secret": "ENDPOINT_API_KEY"},
            "local": {"kind": "openai-compatible", "base_url": "http://127.0.0.1:57726/v1"},
        },
        "agents": {"scout": {"provider": "endpoint", "model": "qwen3", "at": "08:00"},
                   "brief": {"provider": "endpoint", "model": "qwen3", "at": "09:00"},
                   "writer": {"provider": "endpoint", "model": "qwen3", "at": "10:00"}},
        "budget_tokens_per_day": 50_000,
    })
    monkeypatch.setattr(ollama, "start", lambda sysname=None: "starting")
    monkeypatch.setattr(ollama, "resolve_base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(probe, "list_models", lambda base, key="unused", timeout=4: None)
    c.post("/ollama/start")
    cfg = read_yaml(data / "presence.yaml")
    assert cfg["providers"]["local"]["base_url"] == "http://127.0.0.1:11434/v1"
    assert cfg["providers"]["endpoint"]["base_url"] == OWN
    assert cfg["agents"]["scout"]["provider"] == "endpoint" and "writer" in cfg["agents"]
    assert cfg["budget_tokens_per_day"] == 50_000
    # a local entry pointed at a server of the person's own that still answers is left alone
    cfg["providers"]["local"]["base_url"] = "http://localhost:1234/v1"
    write_yaml(data / "presence.yaml", cfg)
    monkeypatch.setattr(probe, "list_models", lambda base, key="unused", timeout=4: ["m"])
    c.post("/ollama/start")
    assert read_yaml(data / "presence.yaml")["providers"]["local"]["base_url"] == (
        "http://localhost:1234/v1")
    # the check route saves the model on the local provider and keeps the others
    monkeypatch.setattr(ollama, "readiness",
                        lambda model, base_url=None: (True, "tool call worked"))
    c.post("/ollama/check", data={"model": "qwen3.5:9b"})
    cfg = read_yaml(data / "presence.yaml")
    assert cfg["agents"]["scout"] == {"provider": "local", "model": "qwen3.5:9b", "at": "08:00"}
    assert cfg["agents"]["writer"]["provider"] == "endpoint"
    assert cfg["providers"]["endpoint"]["base_url"] == OWN


def test_model_test_route_says_what_the_server_can_do(client, monkeypatch):
    c, data = client
    r = c.post("/model/test", follow_redirects=True)
    assert b"save a model first" in r.data
    c.post("/setup/model", data={"kind": "endpoint", "base_url": OWN, "model": "qwen3"})
    seen = {}

    def fake_probe(base, model, key="unused", *, hints=True, timeout=60):
        seen.update(base=base, model=model, key=key, hints=hints)
        return {"speaks_openai": True, "reached": True, "models": ["qwen3"], "json": "schema",
                "tools": "native", "server": "vLLM", "advice": "vLLM is ready."}

    monkeypatch.setattr(probe, "probe", fake_probe)
    monkeypatch.setattr(probe, "tool_check",
                        lambda provider, model: (True, "tool call worked · 40 tokens"))
    r = c.post("/model/test", follow_redirects=True)
    html = r.data.decode()
    assert seen == {"base": OWN, "model": "qwen3", "key": "unused", "hints": True}
    assert "your own server, qwen3: vLLM · keeps answers in the exact shape · calls tools" in html
    assert "tool call worked · 40 tokens. vLLM is ready." in html
    assert 'class="flash error"' not in html
    monkeypatch.setattr(probe, "probe", lambda *a, **k: {
        "speaks_openai": True, "reached": True, "models": [], "json": "json_object",
        "tools": "unparsed", "server": "vLLM",
        "advice": "vLLM returned tool calls as text — start it with tool calling enabled."})
    monkeypatch.setattr(probe, "tool_check", lambda provider, model: (False, "no tool call"))
    html = c.post("/model/test", follow_redirects=True).data.decode()
    assert 'class="flash error"' in html and "returns tool calls as text" in html
    assert "start it with tool calling enabled" in html
    # the local engine goes through the same button, with its own readiness gate
    c.post("/setup/model", data={"kind": "local", "model": "qwen3.5:9b"})
    monkeypatch.setattr(probe, "probe", fake_probe)
    monkeypatch.setattr(ollama, "readiness",
                        lambda model, base_url=None: (True, "tool call worked · 30 tokens"))
    html = c.post("/model/test", follow_redirects=True).data.decode()
    assert seen["base"] == f"{ollama.resolve_base_url()}/v1" and seen["key"] == "unused"
    assert "on this computer, qwen3.5:9b" in html and "tool call worked · 30 tokens" in html


def test_setup_page_offers_an_own_server(client):
    c, data = client
    html = c.get("/").data.decode()
    assert "Your own server (vLLM, llama.cpp, LM Studio, and others)" in html
    assert "Server address (ends with /v1)" in html and "Test this model" in html
    assert html.count('<use href="#plant-yours"/></svg>your own server') >= 2
    assert '<div id="engine-block" >' in html  # the engine block shows for the local kind
    c.post("/setup/model", data={"kind": "endpoint", "base_url": OWN, "model": "qwen3"})
    html = c.get("/").data.decode()
    assert '<div id="engine-block" hidden>' in html
    assert 'value="endpoint" selected' in html and f'value="{OWN}"' in html


def test_check_has_an_own_server_preset(monkeypatch, capsys):
    preset, model = check.resolve(["endpoint", "http://localhost:8000/v1/", "qwen3"])
    assert preset["base_url"] == "http://localhost:8000/v1" and model == "qwen3"
    assert preset["key_env"] == "ENDPOINT_API_KEY" and preset["key_optional"]
    assert check.resolve(["ollama", "m"]) == (check.PRESETS["ollama"], "m")
    assert check.resolve(["endpoint", "m"]) is None and check.resolve(["nope", "m"]) is None
    from presence.core.providers import ProviderResponse, ToolCall, Usage

    built = {}

    class FakeProvider:
        def __init__(self, api_key="", base_url=None):
            built.update(api_key=api_key, base_url=base_url)

        def complete(self, **kw):
            if not any(e["role"] == "tool" for e in kw["transcript"]):
                return ProviderResponse(
                    tool_calls=[ToolCall(id="1", name="echo", arguments={"text": "presence"})],
                    usage=Usage(input_tokens=5, output_tokens=5))
            return ProviderResponse(text="DONE", usage=Usage(input_tokens=5, output_tokens=5))

    monkeypatch.setattr(check, "OpenAICompatProvider", FakeProvider)
    monkeypatch.delenv("ENDPOINT_API_KEY", raising=False)
    monkeypatch.setattr(check.sys, "argv", ["check", "endpoint", OWN, "qwen3"])
    assert check.main() == 0
    assert built == {"api_key": "", "base_url": OWN}
    assert "M1 CHECK PASS" in capsys.readouterr().out
    monkeypatch.setattr(check.sys, "argv", ["check", "endpoint"])
    assert check.main() == 2
