"""One JSON answer that fits a schema, on any OpenAI-style server — no network."""

import pytest
from fake_openai import Status, install, reply

from presence.core import structured

NAME = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
NESTED = {
    "type": "object",
    "properties": {
        "work": {"type": "array", "items": {
            "type": "object",
            "properties": {"company": {"type": "string"}, "years": {"type": "integer"},
                           "score": {"type": "number"}, "current": {"type": "boolean"}},
            "required": ["company"]}},
    },
    "required": ["work"],
}


def test_own_server_goes_through_v1_with_nested_schema_and_thinking_off(monkeypatch):
    seen = install(monkeypatch, reply('{"name": "Ada"}'))
    out = structured.ask_json("endpoint", "qwen", "sys", "prompt", NAME,
                              base_url="http://localhost:8000/v1", api_key="tok")
    assert out == {"name": "Ada"}
    assert seen["base_url"] == "http://localhost:8000/v1" and seen["api_key"] == "tok"
    call = seen["calls"][0]
    assert call["model"] == "qwen" and call["temperature"] == 0
    assert call["messages"] == [{"role": "system", "content": "sys"},
                                {"role": "user", "content": "prompt"}]
    assert call["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "answer", "schema": NAME}}
    assert call["extra_body"] == {"reasoning_effort": "none",
                                  "chat_template_kwargs": {"enable_thinking": False}}
    assert structured.LAST_USAGE == {"input_tokens": 7, "output_tokens": 3}


def test_own_server_without_a_key_sends_a_placeholder(monkeypatch):
    seen = install(monkeypatch, reply('{"name": "Ada"}'))
    structured.ask_json("endpoint", "m", "s", "p", NAME, base_url="http://box:8000/v1")
    assert seen["api_key"] == "unused"


def test_local_engine_uses_its_v1_door(monkeypatch):
    seen = install(monkeypatch, reply('```json\n{"name": "Ada"}\n```'))
    out = structured.ask_json("local", "qwen3.5:9b", "s", "p", NAME,
                              base_url="http://127.0.0.1:57726/v1")
    assert out == {"name": "Ada"}
    assert seen["base_url"] == "http://127.0.0.1:57726/v1" and seen["api_key"] == "ollama"
    assert "extra_body" in seen["calls"][0]
    seen = install(monkeypatch, reply('{"name": "Ada"}'))
    structured.ask_json("local", "m", "s", "p", NAME)  # no address: the usual port
    assert seen["base_url"] == "http://127.0.0.1:11434/v1"


def test_openai_itself_gets_no_server_only_hints(monkeypatch):
    seen = install(monkeypatch, reply('{"name": "Ada"}'))
    structured.ask_json("openai", "gpt-4o-mini", "s", "p", NAME, api_key="sk-x")
    assert seen["base_url"] is None and seen["api_key"] == "sk-x"
    assert "extra_body" not in seen["calls"][0]
    assert seen["calls"][0]["response_format"]["type"] == "json_schema"


def test_reasoning_is_dropped_and_thinking_alone_is_an_error(monkeypatch):
    install(monkeypatch, reply('<think>let me see</think>{"name": "Ada"}',
                               reasoning_content="let me see"))
    assert structured.ask_json("endpoint", "m", "s", "p", NAME, base_url="http://x/v1") == {
        "name": "Ada"}
    install(monkeypatch, reply("", reasoning="I only thought"))
    with pytest.raises(structured.StructuredError, match="only thought"):
        structured.ask_json("endpoint", "m", "s", "p", NAME, base_url="http://x/v1")
    install(monkeypatch, reply("<think>never closed", thinking="x"))
    with pytest.raises(structured.StructuredError, match="only thought"):
        structured.ask_json("endpoint", "m", "s", "p", NAME, base_url="http://x/v1")


def test_a_200_is_checked_against_the_schema(monkeypatch):
    install(monkeypatch, reply('{"name": 3}'))
    with pytest.raises(structured.StructuredError, match="did not fit the expected shape"):
        structured.ask_json("endpoint", "m", "s", "p", NAME, base_url="http://x/v1")
    install(monkeypatch, reply('{"work": [{"company": "Acme", "years": "two"}]}'))
    with pytest.raises(structured.StructuredError, match="'years' should be integer"):
        structured.ask_json("endpoint", "m", "s", "p", NESTED, base_url="http://x/v1")
    install(monkeypatch, reply('{"work": [{"years": 2}]}'))
    with pytest.raises(structured.StructuredError, match="missing 'company'"):
        structured.ask_json("endpoint", "m", "s", "p", NESTED, base_url="http://x/v1")
    install(monkeypatch, reply('{"work": "none"}'))
    with pytest.raises(structured.StructuredError, match="should be array"):
        structured.ask_json("endpoint", "m", "s", "p", NESTED, base_url="http://x/v1")


def test_check_shape_covers_every_type():
    ok = {"work": [{"company": "Acme", "years": 2, "score": 4.5, "current": True}]}
    assert structured.check_shape(ok, NESTED) == ""
    assert structured.check_shape({"work": []}, NESTED) == ""
    assert "should be integer" in structured.check_shape(
        {"work": [{"company": "A", "years": True}]}, NESTED)
    assert "should be number" in structured.check_shape(
        {"work": [{"company": "A", "score": "high"}]}, NESTED)
    assert "should be boolean" in structured.check_shape(
        {"work": [{"company": "A", "current": "yes"}]}, NESTED)
    assert "should be object" in structured.check_shape([], NESTED)
    assert structured.check_shape({"a": None}, {"type": "object", "properties": {
        "a": {"type": ["string", "null"]}}}) == ""
    assert structured.check_shape({"a": 1}, {"type": "object", "properties": {
        "a": {"type": "string"}}}) == "'a' should be string"


def test_an_older_engine_falls_back_to_the_native_door(monkeypatch):
    install(monkeypatch, Status(400, "invalid response_format: json_schema is not supported"))
    seen = {}

    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"message": {"content": '{"name": "Ada"}', "thinking": "hm"},
                    "prompt_eval_count": 11, "eval_count": 4}

    def post(url, json=None, timeout=None):
        seen.update(url=url, body=json)
        return R()

    monkeypatch.setattr(structured.requests, "post", post)
    out = structured.ask_json("local", "qwen3.5:9b", "sys", "p", NAME,
                              base_url="http://127.0.0.1:57726/v1")
    assert out == {"name": "Ada"} and seen["url"] == "http://127.0.0.1:57726/api/chat"
    body = seen["body"]
    assert body["format"] == NAME and body["think"] is False and body["stream"] is False
    assert body["options"] == {"temperature": 0}
    assert structured.LAST_USAGE == {"input_tokens": 11, "output_tokens": 4}


def test_the_fallback_is_only_for_a_rejected_structured_call(monkeypatch):
    calls = []
    monkeypatch.setattr(structured.requests, "post",
                        lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
                            structured.requests.ConnectionError()))
    install(monkeypatch, Status(400, "model 'nope' not found, try pulling it first"))
    with pytest.raises(structured.StructuredError, match="not found"):
        structured.ask_json("local", "nope", "s", "p", NAME)
    assert calls == []  # a model problem is not a transport problem
    install(monkeypatch, Status(404, "Not Found"))
    with pytest.raises(structured.StructuredError, match="did not answer"):
        structured.ask_json("local", "m", "s", "p", NAME)  # the native door's own error
    assert len(calls) == 1
    install(monkeypatch, Status(400, "response_format unsupported"))
    with pytest.raises(structured.StructuredError, match="response_format unsupported"):
        structured.ask_json("endpoint", "m", "s", "p", NAME, base_url="http://box:8000/v1")
    assert len(calls) == 2  # tried, then the original reason is what the person sees
    install(monkeypatch, Status(400, "response_format unsupported"))
    with pytest.raises(structured.StructuredError):
        structured.ask_json("openai", "m", "s", "p", NAME, api_key="k")
    assert len(calls) == 2  # never for OpenAI itself


def test_budget_is_charged_with_the_v1_usage(monkeypatch):
    install(monkeypatch, reply('{"name": "Ada"}', prompt_tokens=100, completion_tokens=20))
    charged = []

    class Budget:
        def ensure_available(self):
            pass

        def charge(self, usage):
            charged.append(usage.total)

    structured.ask_json("endpoint", "m", "s", "p", NAME, base_url="http://x/v1",
                        budget=Budget())
    assert charged == [120]
