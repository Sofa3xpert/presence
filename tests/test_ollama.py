"""Ollama step: detection ladder, recommendations, pull progress, routes — no network."""

from presence.app import ollama, server
from presence.core.providers import ProviderResponse, ToolCall, Usage


def test_download_url_and_recommendation():
    assert ollama.download_url("Darwin").endswith("/mac")
    assert ollama.download_url("Windows").endswith("/windows")
    assert ollama.download_url("Plan9") == "https://ollama.com/download"
    assert ollama.recommend_model(8)[0] == "llama3.2:3b"
    assert ollama.recommend_model(16)[0] == "qwen3.5:9b"
    assert ollama.recommend_model(None)[0] == "qwen3.5:9b"


def test_server_status_parses_and_fails_soft(monkeypatch):
    class R:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    def fake_get(url, timeout):
        return (
            R({"version": "0.12.1"})
            if url.endswith("/api/version")
            else R({"models": [{"name": "qwen3.5:9b"}, {"name": "llama3.2:3b"}]})
        )

    monkeypatch.setattr(ollama.requests, "get", fake_get)
    s = ollama.server_status()
    assert s == {"running": True, "version": "0.12.1", "models": ["llama3.2:3b", "qwen3.5:9b"]}
    monkeypatch.setattr(
        ollama.requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("down"))
    )
    assert ollama.server_status()["running"] is False


def test_pull_progress_parsing():
    assert ollama.parse_pull_line('{"status":"pulling abc","total":1000,"completed":250}') == {
        "status": "pulling abc",
        "done_pct": 25.0,
        "error": None,
    }
    assert ollama.parse_pull_line("not json")["done_pct"] is None
    assert ollama.parse_pull_line('{"error":"model not found"}')["error"] == "model not found"


def test_readiness_uses_the_agent_loop(monkeypatch):
    class FakeProvider:
        def __init__(self, **kw):
            pass

        def complete(self, **kw):
            if not any(e["role"] == "tool" for e in kw["transcript"]):
                return ProviderResponse(
                    tool_calls=[ToolCall(id="1", name="echo", arguments={"text": "presence"})],
                    usage=Usage(input_tokens=5, output_tokens=5),
                )
            return ProviderResponse(text="DONE", usage=Usage(input_tokens=5, output_tokens=5))

    import presence.core.providers as providers

    monkeypatch.setattr(providers, "OpenAICompatProvider", FakeProvider)
    ok, detail = ollama.readiness("fake-model")
    assert ok and "tool call worked" in detail


def test_status_route_and_check_route_save_model(tmp_path, monkeypatch):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    c = app.test_client()
    monkeypatch.setattr(
        ollama,
        "status",
        lambda base_url=None: {
            "running": False,
            "version": "",
            "models": [],
            "installed": False,
            "download_url": "https://ollama.com/download/mac",
            "system": "Darwin",
            "ram_gb": 16.0,
            "recommended": "qwen3.5:9b",
            "recommended_size": "about 6 GB",
        },
    )
    j = c.get("/ollama/status").get_json()
    assert j["download_url"].endswith("/mac") and j["recommended"] == "qwen3.5:9b"
    monkeypatch.setattr(
        ollama, "readiness", lambda model, base_url=None: (True, "tool call worked · 40 tokens")
    )
    r = c.post("/ollama/check", data={"model": "qwen3.5:9b"}, follow_redirects=True)
    assert b"is ready" in r.data
    from presence.app.config_io import read_yaml

    assert read_yaml(tmp_path / "presence.yaml")["agents"]["scout"]["model"] == "qwen3.5:9b"
    monkeypatch.setattr(
        ollama, "readiness", lambda model, base_url=None: (False, "did not call the tool")
    )
    r = c.post("/ollama/check", data={"model": "tiny"}, follow_redirects=True)
    assert b"did not call the tool" in r.data
