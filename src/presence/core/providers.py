"""Provider adapters: one neutral chat-with-tools interface over the two API
families Presence supports — Anthropic, and OpenAI-compatible (which covers
OpenAI, Ollama and LM Studio with a base_url switch).

The executor owns a neutral transcript; adapters translate it per call, so
nothing outside this module knows which provider is running.

Neutral transcript entries:
    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [ToolCall, ...]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the arguments object


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class ProviderResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str = ""
    usage: Usage = Field(default_factory=Usage)


class Provider(Protocol):
    def complete(
        self,
        *,
        model: str,
        system: str,
        transcript: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_output_tokens: int,
    ) -> ProviderResponse: ...


# ---------------------------------------------------------------- translation

def anthropic_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.parameters}
        for t in tools
    ]


def openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def to_anthropic_messages(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic wants tool results as user-role tool_result blocks; consecutive
    tool entries merge into a single user turn."""
    messages: list[dict[str, Any]] = []
    for entry in transcript:
        role = entry["role"]
        if role == "user":
            messages.append({"role": "user", "content": entry["content"]})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if entry.get("content"):
                blocks.append({"type": "text", "text": entry["content"]})
            for call in entry.get("tool_calls", []):
                blocks.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                )
            messages.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": entry["tool_call_id"],
                "content": entry["content"],
            }
            if messages and messages[-1]["role"] == "user" and isinstance(
                messages[-1]["content"], list
            ):
                messages[-1]["content"].append(block)
            else:
                messages.append({"role": "user", "content": [block]})
    return messages


def to_openai_messages(system: str, transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for entry in transcript:
        role = entry["role"]
        if role == "user":
            messages.append({"role": "user", "content": entry["content"]})
        elif role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": entry.get("content") or None}
            if entry.get("tool_calls"):
                msg["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in entry["tool_calls"]
                ]
            messages.append(msg)
        elif role == "tool":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": entry["tool_call_id"],
                    "content": entry["content"],
                }
            )
    return messages


# ------------------------------------------------------------------ adapters

class AnthropicProvider:
    def __init__(self, api_key: str):
        import anthropic  # lazy: only needed when this provider is configured

        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(
        self,
        *,
        model: str,
        system: str,
        transcript: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_output_tokens: int,
    ) -> ProviderResponse:
        response = self._client.messages.create(
            model=model,
            system=system,
            messages=to_anthropic_messages(transcript),
            tools=anthropic_tools(tools) if tools else [],
            max_tokens=max_output_tokens,
        )
        text = ""
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        return ProviderResponse(
            text=text,
            tool_calls=calls,
            stop_reason=response.stop_reason or "",
            usage=Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
        )


class OpenAICompatProvider:
    """OpenAI, and every OpenAI-compatible server: Ollama (`http://localhost:11434/v1`),
    LM Studio (`http://localhost:1234/v1`), and friends. Local servers accept any api_key."""

    def __init__(self, api_key: str = "unused", base_url: str | None = None):
        import openai  # lazy: only needed when this provider is configured

        self._client = openai.OpenAI(api_key=api_key or "unused", base_url=base_url)

    def complete(
        self,
        *,
        model: str,
        system: str,
        transcript: list[dict[str, Any]],
        tools: list[ToolSpec],
        max_output_tokens: int,
    ) -> ProviderResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(system, transcript),
            "max_tokens": max_output_tokens,
        }
        if tools:
            kwargs["tools"] = openai_tools(tools)
        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        calls: list[ToolCall] = []
        for call in choice.message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))
        usage = response.usage
        return ProviderResponse(
            text=choice.message.content or "",
            tool_calls=calls,
            stop_reason=choice.finish_reason or "",
            usage=Usage(
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            ),
        )
