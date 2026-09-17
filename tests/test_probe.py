"""What a server can do, from three tiny requests — with fake servers, no network."""

from presence.core import probe


class R:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self.payload, self.text = status, payload, text

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


def chat(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return R(200, {"choices": [{"message": msg}]})


def fake_server(monkeypatch, gets, posts):
    """gets: url suffix -> R; posts: list of R answered in order (last repeats).
    Returns the list of posted bodies."""
    bodies = []

    def get(url, headers=None, timeout=None):
        for suffix, r in gets.items():
            if url.endswith(suffix):
                return r
        raise probe.requests.ConnectionError(url)

    def post(url, json=None, headers=None, timeout=None):
        bodies.append((url, json, headers))
        return posts.pop(0) if len(posts) > 1 else posts[0]

    monkeypatch.setattr(probe.requests, "get", get)
    monkeypatch.setattr(probe.requests, "post", post)
    return bodies


def test_a_capable_server_is_recognised_and_praised(monkeypatch):
    bodies = fake_server(
        monkeypatch,
        {"/v1/models": R(200, {"data": [{"id": "qwen3", "owned_by": "vllm"},
                                        {"id": "other", "owned_by": "vllm"}]})},
        [chat('{"ok": true}'),
         chat(None, [{"id": "1", "type": "function",
                      "function": {"name": "echo", "arguments": '{"text":"presence"}'}}])],
    )
    out = probe.probe("http://localhost:8000/v1", "qwen3", "tok")
    assert out["speaks_openai"] and out["models"] == ["other", "qwen3"]
    assert out["json"] == "schema" and out["tools"] == "native" and out["server"] == "vLLM"
    assert out["advice"].startswith("vLLM is ready")
    url, body, headers = bodies[0]
    assert url == "http://localhost:8000/v1/chat/completions"
    assert headers == {"Authorization": "Bearer tok"} and body["temperature"] == 0
    assert body["response_format"]["json_schema"]["schema"] == probe.PROBE_SCHEMA
    assert body["reasoning_effort"] == "none"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert bodies[1][1]["tools"] == [probe.ECHO_TOOL] and "tool_choice" not in bodies[1][1]
    assert "vLLM" in probe.describe(out) and "calls tools" in probe.describe(out)


def test_json_object_only_and_tool_calls_as_text(monkeypatch):
    fake_server(
        monkeypatch,
        {"/v1/models": R(200, {"data": [{"id": "m", "owned_by": "llamacpp"}]})},
        [chat("Sure! Here you go."),  # the schema was ignored
         chat('{"ok": true}'),  # json_object honoured
         chat('<tool_call>{"name": "echo", "arguments": {"text": "presence"}}</tool_call>')],
    )
    out = probe.probe("http://localhost:8080/v1", "m")
    assert out["json"] == "json_object" and out["tools"] == "unparsed"
    assert out["server"] == "llama.cpp"
    assert "returned tool calls as text" in out["advice"]
    assert "--enable-auto-tool-choice" not in out["advice"]  # that hint is vLLM's


def test_neither_json_nor_tools_with_the_vllm_hint(monkeypatch):
    fake_server(
        monkeypatch,
        {"/v1/models": R(200, {"data": [{"id": "m", "owned_by": "vllm"}]})},
        [R(400, {"error": {"message": "response_format is not supported"}}),
         R(400, {"error": {"message": "response_format is not supported"}}),
         R(400, {"error": {"message": '"auto" tool choice requires --enable-auto-tool-choice'}})],
    )
    out = probe.probe("http://localhost:8000/v1", "m")
    assert out["json"] == "prompt" and out["tools"] == "none"
    assert "--enable-auto-tool-choice --tool-call-parser qwen3_coder" in out["advice"]
    assert "cannot be held to JSON" in probe.describe(out)


def test_a_plain_answer_to_the_tool_request_means_no_tools(monkeypatch):
    fake_server(
        monkeypatch,
        {"/v1/models": R(200, {"data": [{"id": "m", "owned_by": "library"}]})},
        [chat('{"ok": true}'), chat("I cannot call tools, sorry.")],
    )
    out = probe.probe("http://localhost:11434/v1", "m")
    assert out["tools"] == "none" and out["server"] == "Ollama"
    assert out["advice"].endswith("pick a model that supports tool calling.")
    assert "start the server" not in out["advice"]  # Ollama has no such switch


def test_unreachable_and_wrong_door(monkeypatch):
    fake_server(monkeypatch, {}, [R(500)])
    out = probe.probe("http://localhost:9/v1", "m")
    assert out == {"speaks_openai": False, "reached": False, "models": [], "json": "prompt",
                   "tools": "none", "server": "",
                   "advice": out["advice"]}
    assert "could not reach" in out["advice"]
    # something answers, but not the OpenAI style — an Ollama root without /v1
    fake_server(monkeypatch, {"/models": R(404, None, "404 page not found"),
                              "/api/tags": R(200, {"models": []})}, [R(404)])
    out = probe.probe("http://localhost:11434", "m")
    assert not out["speaks_openai"] and out["reached"] and out["server"] == "Ollama"
    assert "ends with /v1" in out["advice"]
    assert probe.describe(out) == "does not answer in the OpenAI style"


def test_fingerprints_from_the_extra_doors(monkeypatch):
    fake_server(monkeypatch, {"/v1/models": R(200, {"data": [{"id": "m"}]}),
                              "/api/v0/models": R(200, {"data": []})}, [chat('{"ok": true}')])
    assert probe.probe("http://localhost:1234/v1", "m")["server"] == "LM Studio"
    fake_server(monkeypatch, {"/v1/models": R(200, {"data": [{"id": "m"}]}),
                              "/props": R(200, {"default_generation_settings": {}})},
                [chat('{"ok": true}')])
    assert probe.probe("http://localhost:8080/v1", "m")["server"] == "llama.cpp"
    fake_server(monkeypatch, {"/v1/models": R(200, {"data": [{"id": "m"}]})},
                [chat('{"ok": true}')])
    assert probe.probe("http://box/v1", "m")["server"] == ""


def test_a_model_the_server_does_not_list(monkeypatch):
    fake_server(monkeypatch, {"/v1/models": R(200, {"data": [{"id": "a"}, {"id": "b"}]})},
                [chat('{"ok": true}')])
    out = probe.probe("http://box/v1", "zzz")
    assert "does not list a model called zzz" in out["advice"] and "a, b" in out["advice"]
    out = probe.probe("http://box/v1", "a")  # exact match is fine
    assert "does not list" not in out["advice"]


def test_list_models(monkeypatch):
    fake_server(monkeypatch, {"/v1/models": R(200, {"data": [{"id": "b"}, {"id": "a"}]})}, [])
    assert probe.list_models("http://box/v1/") == ["a", "b"]
    fake_server(monkeypatch, {"/v1/models": R(404, None)}, [])
    assert probe.list_models("http://box/v1") is None
    fake_server(monkeypatch, {}, [])
    assert probe.list_models("http://nowhere/v1") is None


def test_tool_check_runs_the_agent_loop_on_any_provider():
    from presence.core.providers import ProviderResponse, ToolCall, Usage

    class Fake:
        def complete(self, **kw):
            if not any(e["role"] == "tool" for e in kw["transcript"]):
                return ProviderResponse(
                    tool_calls=[ToolCall(id="1", name="echo", arguments={"text": "presence"})],
                    usage=Usage(input_tokens=5, output_tokens=5))
            return ProviderResponse(text="DONE", usage=Usage(input_tokens=5, output_tokens=5))

    ok, detail = probe.tool_check(Fake(), "m")
    assert ok and detail == "tool call worked · 20 tokens"

    class Mute:
        def complete(self, **kw):
            return ProviderResponse(text="DONE", usage=Usage())

    ok, detail = probe.tool_check(Mute(), "m")
    assert not ok and "did not call the tool" in detail
