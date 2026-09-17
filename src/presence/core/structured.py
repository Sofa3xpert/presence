"""One question, one JSON answer that fits a schema — on whichever model the
person chose.

Two transports, one contract:
  * every OpenAI-compatible server — the local engine (Ollama), the person's
    own server (vLLM, llama.cpp, LM Studio, and others) and OpenAI itself: the
    chat completions call with ``response_format`` of type ``json_schema`` in
    the nested shape (llama.cpp silently ignores a flat one), temperature 0,
    and the hints that make thinking models answer directly instead of
    reasoning first (Ollama maps them to ``format`` and ``think: false``);
  * Anthropic: a single forced tool whose input schema is the schema.

An older Ollama that rejects ``response_format`` falls back to its native chat
endpoint with ``format`` set to the schema. Whatever the transport, the answer
is checked against the schema here — a 200 is not proof the schema was honoured.

The section-by-section idea and the resume schema in ``presence.app.cvextract``
are adapted from HackerRank's hiring-agent (MIT)."""

from __future__ import annotations

import json
import re
from typing import Any

import requests

from presence.core.providers import Usage

DEFAULT_OLLAMA = "http://127.0.0.1:11434"
# Answer directly: no reasoning first. Understood by Ollama (think: false),
# llama.cpp and vLLM (chat template switch) and ignored by servers without it.
# OpenAI's own API rejects unknown fields, so these go to self-hosted servers only.
THINK_OFF: dict[str, Any] = {
    "reasoning_effort": "none",
    "chat_template_kwargs": {"enable_thinking": False},
}
_REASONING_FIELDS = ("reasoning_content", "reasoning", "thinking")


class StructuredError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _json_from_text(text: str) -> dict[str, Any]:
    """The JSON object in a model reply, even when wrapped in prose or fences."""
    text = (text or "").strip()
    try:
        out = json.loads(text)
        if isinstance(out, dict):
            return out
    except ValueError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise StructuredError("the model did not answer with JSON")
    try:
        out = json.loads(m.group(0))
    except ValueError as exc:
        raise StructuredError("the model's JSON could not be read") from exc
    if not isinstance(out, dict):
        raise StructuredError("the model did not answer with a JSON object")
    return out


def strip_thinking(text: str) -> str:
    """The reply without any <think>…</think> block; an unclosed one is all thinking."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


def answer_text(message: Any) -> str:
    """The answer part of a chat reply — never the reasoning, whichever field or
    tag carries it (vLLM's reasoning_content, Ollama's reasoning, others' thinking)."""
    get = message.get if isinstance(message, dict) else lambda k, d=None: getattr(message, k, d)
    content = strip_thinking(get("content") or "")
    if not content and any(get(f) for f in _REASONING_FIELDS):
        raise StructuredError("the model only thought and gave no answer")
    return content


# ---------------------------------------------------------------- validation

def _type_ok(value: Any, typ: str) -> bool:
    if typ == "null":
        return value is None
    if typ == "boolean":
        return isinstance(value, bool)
    if typ == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    py = {"object": dict, "array": list, "string": str}.get(typ)
    return True if py is None else isinstance(value, py)


def check_shape(value: Any, schema: dict[str, Any], where: str = "the answer") -> str:
    """'' when the value fits the schema's shape (type, required keys, property
    and item types, recursively); otherwise one plain phrase saying what is off."""
    typ = schema.get("type")
    types = typ if isinstance(typ, list) else [typ] if typ else []
    if types and not any(_type_ok(value, t) for t in types):
        return f"{where} should be {' or '.join(types)}"
    if isinstance(value, dict):
        for key in schema.get("required") or []:
            if key not in value:
                return f"{where} is missing '{key}'"
        for key, sub in (schema.get("properties") or {}).items():
            if key in value and isinstance(sub, dict):
                if why := check_shape(value[key], sub, f"'{key}'"):
                    return why
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            if why := check_shape(item, schema["items"], f"{where} item {i + 1}"):
                return why
    return ""


# ---------------------------------------------------------------- transports

def ollama_json(base_url: str, model: str, system: str, prompt: str, schema: dict[str, Any],
                timeout: float = 300) -> dict[str, Any]:
    """Ollama's native structured output: /api/chat with format=<schema>. base_url has no
    /v1. Only the fallback for an older engine that rejects response_format."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "format": schema,
        "stream": False,
        "think": False,  # thinking models otherwise spend the whole budget reasoning
        "options": {"temperature": 0},  # default context: a bigger one can spill a 9B model to CPU
    }
    try:
        r = requests.post(f"{base_url.rstrip('/')}/api/chat", json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise StructuredError(f"the local engine did not answer ({type(exc).__name__})") from exc
    if r.status_code != 200:
        raise StructuredError(f"the local engine answered {r.status_code}: {r.text[:160]}",
                              status=r.status_code)
    body_out = r.json()
    LAST_USAGE.update(input_tokens=int(body_out.get("prompt_eval_count") or 0),
                      output_tokens=int(body_out.get("eval_count") or 0))
    return _json_from_text(answer_text(body_out.get("message") or {}))


def openai_json(api_key: str, base_url: str | None, model: str, system: str, prompt: str,
                schema: dict[str, Any], name: str = "answer", *,
                hints: bool = True) -> dict[str, Any]:
    """POST {base}/chat/completions with the nested json_schema response_format.
    ``hints`` adds the answer-directly fields for self-hosted servers."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key or "unused", base_url=base_url)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": name, "schema": schema}},
        "temperature": 0,
    }
    if hints:
        kwargs["extra_body"] = dict(THINK_OFF)
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:
        raise StructuredError(f"{type(exc).__name__}: {str(exc)[:160]}",
                              status=getattr(exc, "status_code", None)) from exc
    u = getattr(resp, "usage", None)
    LAST_USAGE.update(input_tokens=int(getattr(u, "prompt_tokens", 0) or 0),
                      output_tokens=int(getattr(u, "completion_tokens", 0) or 0))
    return _json_from_text(answer_text(resp.choices[0].message))


def anthropic_json(api_key: str, model: str, system: str, prompt: str, schema: dict[str, Any],
                   name: str = "answer") -> dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=[{"name": name, "description": "Record the answer.", "input_schema": schema}],
            tool_choice={"type": "tool", "name": name},
            temperature=0,
        )
    except Exception as exc:
        raise StructuredError(f"{type(exc).__name__}: {str(exc)[:160]}") from exc
    u = getattr(resp, "usage", None)
    LAST_USAGE.update(input_tokens=int(getattr(u, "input_tokens", 0) or 0),
                      output_tokens=int(getattr(u, "output_tokens", 0) or 0))
    for block in resp.content:
        if getattr(block, "type", "") == "tool_use":
            return dict(block.input)
    raise StructuredError("the model did not use the answer tool")


LAST_USAGE: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}  # of the latest call


def ask_json(kind: str, model: str, system: str, prompt: str, schema: dict[str, Any], *,
             base_url: str | None = None, api_key: str = "", budget: Any = None) -> dict[str, Any]:
    """Dispatch on the provider kind: 'local' (the Ollama engine), 'endpoint' (the
    person's own server), 'openai', 'anthropic'. The answer is checked against the
    schema before it is returned. With a DailyBudget, the call is refused once the
    day's cap is spent and charged with the tokens it used (charter rule 4)."""
    if budget is not None:
        budget.ensure_available()
    LAST_USAGE.update(input_tokens=0, output_tokens=0)
    try:
        out = _dispatch(kind, model, system, prompt, schema, base_url=base_url, api_key=api_key)
        if why := check_shape(out, schema):
            raise StructuredError(f"the model's answer did not fit the expected shape: {why}")
        return out
    finally:
        if budget is not None and (LAST_USAGE["input_tokens"] or LAST_USAGE["output_tokens"]):
            budget.charge(Usage(**LAST_USAGE))


def _lacks_response_format(exc: StructuredError) -> bool:
    """Did the server reject the structured call itself (an older engine), rather
    than the model or the prompt?"""
    if exc.status == 404:
        return True
    text = str(exc).lower()
    return exc.status == 400 and any(w in text for w in ("response_format", "json_schema",
                                                          "format"))


def _dispatch(kind: str, model: str, system: str, prompt: str, schema: dict[str, Any], *,
              base_url: str | None = None, api_key: str = "") -> dict[str, Any]:
    if kind == "anthropic":
        return anthropic_json(api_key, model, system, prompt, schema)
    if kind == "local":
        root = (base_url or DEFAULT_OLLAMA).removesuffix("/v1").rstrip("/")
        base, key = f"{root}/v1", "ollama"
    elif kind == "openai":
        base, key = base_url or None, api_key
    else:  # "endpoint": the person's own server, its address used as given
        base, key = base_url, api_key or "unused"
    try:
        return openai_json(key, base, model, system, prompt, schema, hints=kind != "openai")
    except StructuredError as exc:
        if kind == "openai" or not base or not _lacks_response_format(exc):
            raise
        try:  # an older Ollama: the native endpoint, one level up from /v1
            return ollama_json(base.removesuffix("/v1").rstrip("/"), model, system, prompt,
                               schema)
        except StructuredError as native:
            raise (native if kind == "local" else exc) from exc
