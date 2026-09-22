"""What can this server do? Three tiny requests, then one plain sentence.

Presence talks to every self-hosted model through the OpenAI-style API that
Ollama, vLLM, llama.cpp, LM Studio and friends all offer. They differ in what
they honour: some hold an answer to a JSON schema, some only to "any JSON",
some hand tool calls back parsed and some as text. The probe finds out, before
a person trusts a server with their CV, and says what to change if anything.
Nothing here sends anything about the person: the requests carry one word."""

from __future__ import annotations

from typing import Any

import requests

from presence.core.structured import THINK_OFF, _json_from_text, strip_thinking

PROBE_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
ECHO_TOOL = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Echo the text back.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
}
# Tool calls that reached us as text: the server has no parser for this model's format.
TEXT_CALL_MARKERS = ("<tool_call", "<function=", "<|tool", "[tool_calls]", "<function_call")
VLLM_FLAGS = "--enable-auto-tool-choice --tool-call-parser qwen3_coder"


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key or 'unused'}"}


def _get_json(url: str, api_key: str = "unused", timeout: float = 4) -> tuple[int, Any]:
    """(status, parsed body) — status 0 when the server did not answer at all."""
    try:
        r = requests.get(url, headers=_headers(api_key), timeout=timeout)
    except requests.RequestException:
        return 0, None
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


def list_models(base_url: str, api_key: str = "unused", timeout: float = 4) -> list[str] | None:
    """The ids GET {base}/models lists — None when nothing OpenAI-style answers there."""
    status, body = _get_json(f"{base_url.rstrip('/')}/models", api_key, timeout)
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return None
    return sorted(str(m.get("id", "")) for m in body["data"] if isinstance(m, dict))


def _chat(base_url: str, api_key: str, body: dict[str, Any],
          timeout: float) -> tuple[int, dict[str, Any], str]:
    """(status, the reply's message, error text) for one chat completion."""
    try:
        r = requests.post(f"{base_url.rstrip('/')}/chat/completions", json=body,
                          headers=_headers(api_key), timeout=timeout)
    except requests.RequestException as exc:
        return 0, {}, type(exc).__name__
    try:
        out = r.json()
    except ValueError:
        out = {}
    if r.status_code != 200:
        err = out.get("error") if isinstance(out, dict) else None
        text = err.get("message", "") if isinstance(err, dict) else (err or r.text or "")
        return r.status_code, {}, str(text)[:300]
    choices = out.get("choices") if isinstance(out, dict) else None
    msg = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
    return r.status_code, msg if isinstance(msg, dict) else {}, ""


def _tiny(model: str, content: str, hints: bool, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": content}],
                            "temperature": 0, "max_tokens": 300, **extra}
    if hints:
        body.update(THINK_OFF)
    return body


def probe_json(base_url: str, model: str, api_key: str = "unused", *, hints: bool = True,
               timeout: float = 60) -> str:
    """'schema' when the server holds the answer to a JSON schema, 'json_object'
    when only to "any JSON", 'prompt' when neither (the prompt must ask, and the
    answer is checked afterwards)."""
    ask = 'Reply with a JSON object with one key "ok" whose value is true.'
    nested = {"type": "json_schema", "json_schema": {"name": "probe", "schema": PROBE_SCHEMA}}
    status, msg, _ = _chat(base_url, api_key, _tiny(model, ask, hints, response_format=nested),
                           timeout)
    if status == 200 and isinstance(_answer(msg).get("ok"), bool):
        return "schema"
    status, msg, _ = _chat(base_url, api_key,
                           _tiny(model, ask, hints, response_format={"type": "json_object"}),
                           timeout)
    if status == 200 and _answer(msg):
        return "json_object"
    return "prompt"


def _answer(msg: dict[str, Any]) -> dict[str, Any]:
    try:
        return _json_from_text(strip_thinking(str(msg.get("content") or "")))
    except Exception:
        return {}


def probe_tools(base_url: str, model: str, api_key: str = "unused", *, hints: bool = True,
                timeout: float = 60) -> str:
    """'native' when a tool call comes back parsed, 'unparsed' when it comes back
    as text, 'none' when the server or model cannot call tools."""
    body = _tiny(model, "Call the echo tool once with text='presence'.", hints,
                 tools=[ECHO_TOOL])
    status, msg, err = _chat(base_url, api_key, body, timeout)
    if status != 200:
        return "none"
    if msg.get("tool_calls"):
        return "native"
    text = str(msg.get("content") or "").lower()
    if any(m in text for m in TEXT_CALL_MARKERS):
        return "unparsed"
    if '"echo"' in text and ("argument" in text or "parameter" in text):
        return "unparsed"
    return "none"


def fingerprint(base_url: str, models_body: Any = None, api_key: str = "unused") -> str:
    """Which server this is, best effort: from the model list's owner field, else
    from the one extra endpoint each server family has. '' when unsure."""
    owners = set()
    if isinstance(models_body, dict):
        for m in models_body.get("data") or []:
            if isinstance(m, dict):
                owners.add(str(m.get("owned_by", "")).lower())
    for owner, name in (("vllm", "vLLM"), ("llamacpp", "llama.cpp"), ("library", "Ollama"),
                        ("organization_owner", "LM Studio"), ("nvidia", "NVIDIA NIM"),
                        ("system", "NVIDIA NIM")):
        if owner in owners:
            return name
    root = base_url.rstrip("/").removesuffix("/v1").rstrip("/")
    for path, name, key in (("/props", "llama.cpp", None), ("/api/tags", "Ollama", "models"),
                            ("/api/v0/models", "LM Studio", "data"),
                            ("/v1/health/ready", "NVIDIA NIM", None),
                            ("/version", "vLLM", "version")):
        status, body = _get_json(root + path, api_key, timeout=3)
        if status == 200 and isinstance(body, dict) and (key is None or key in body):
            return name
    return ""


def advice(result: dict[str, Any], model: str) -> str:
    """One sentence a person can act on — or one that says all is well."""
    if not result["speaks_openai"]:
        if result.get("reached"):
            return ("Something answers at that address but not in the OpenAI style — "
                    "check that the address ends with /v1.")
        return ("Presence could not reach a server at that address — check that it is "
                "running and that the address ends with /v1.")
    models = result["models"]
    if models and model not in models and not any(model in m for m in models):
        shown = ", ".join(models[:5]) + (" and more" if len(models) > 5 else "")
        return (f"That server does not list a model called {model} — it offers {shown}; "
                "type one of those.")
    server = result.get("server") or "your server"
    if result["tools"] == "unparsed":
        how = f" (for vLLM: {VLLM_FLAGS})" if server in ("vLLM", "your server") else ""
        return (f"{server} returned tool calls as text — start it with tool calling "
                f"enabled{how}.")
    if result["tools"] == "none":
        if server in ("Ollama", "LM Studio"):
            return (f"{server} did not accept a tool call for {model} — pick a model that "
                    "supports tool calling.")
        how = f" (for vLLM: {VLLM_FLAGS})" if server in ("vLLM", "your server") else ""
        return (f"{server} did not accept a tool call for {model} — pick a model that supports "
                f"tool calling, or start the server with tool calling enabled{how}.")
    if result["json"] == "prompt":
        return (f"{server} cannot be held to a JSON shape, so answers are checked afterwards "
                "and may fail more often — a newer server version usually fixes this.")
    if result["json"] == "json_object":
        return (f"{server} keeps answers as JSON but not to an exact shape, so answers are "
                "checked afterwards — this works, and a newer server version does better.")
    return f"{server} is ready: it lists {model}, keeps answers in shape and calls tools."


def probe(base_url: str, model: str, api_key: str = "unused", *, hints: bool = True,
          timeout: float = 60) -> dict[str, Any]:
    """Everything a person needs to know about a server, in one dict:
    speaks_openai, models, json, tools, server, advice."""
    base_url = base_url.rstrip("/")
    status, body = _get_json(f"{base_url}/models", api_key)
    speaks = (status == 200 and isinstance(body, dict) and isinstance(body.get("data"), list))
    result: dict[str, Any] = {
        "speaks_openai": speaks,
        "reached": status != 0,
        "models": (sorted(str(m.get("id", "")) for m in body["data"] if isinstance(m, dict))
                   if speaks else []),
        "json": "prompt",
        "tools": "none",
        "server": fingerprint(base_url, body if speaks else None, api_key) if status else "",
    }
    if speaks:
        result["json"] = probe_json(base_url, model, api_key, hints=hints, timeout=timeout)
        result["tools"] = probe_tools(base_url, model, api_key, hints=hints, timeout=timeout)
    result["advice"] = advice(result, model)
    return result


def tool_check(provider: Any, model: str) -> tuple[bool, str]:
    """Can this model drive the agent loop on this provider? The echo-tool gate:
    the model must call one tool and then say it is done."""
    from presence.core.executor import AgentSpec, Tool, run_agent
    from presence.core.providers import ToolSpec

    seen: list[str] = []
    echo = Tool(
        spec=ToolSpec(name="echo", description="Echo the text back.",
                      parameters=ECHO_TOOL["function"]["parameters"]),
        handler=lambda text: (seen.append(text), f"echoed: {text}")[1],
    )
    try:
        result = run_agent(
            AgentSpec(name="ready", model=model, system="Follow the task exactly.",
                      max_turns=4, max_output_tokens=200),
            "Call the echo tool once with text='presence'. Then reply DONE.",
            [echo],
            provider,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:140]}"
    ok = result.stopped == "done" and any("presence" in s.lower() for s in seen)
    return ok, (f"tool call worked · {result.usage.total} tokens" if ok
                else "the model did not call the tool — pick a model that supports tools")


def describe(result: dict[str, Any]) -> str:
    """The probe's findings as a few plain words, for a page."""
    if not result["speaks_openai"]:
        return "does not answer in the OpenAI style"
    json_words = {"schema": "keeps answers in the exact shape",
                  "json_object": "keeps answers as JSON",
                  "prompt": "cannot be held to JSON"}
    tool_words = {"native": "calls tools", "unparsed": "returns tool calls as text",
                  "none": "cannot call tools"}
    who = result.get("server") or "an OpenAI-style server"
    return f"{who} · {json_words[result['json']]} · {tool_words[result['tools']]}"

