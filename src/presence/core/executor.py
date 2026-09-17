"""The agent executor — Presence's provider-agnostic tool-use loop.

An agent is a system prompt plus a set of tools, run against any configured
provider with hard caps: max turns, max output tokens per call, and the
shared daily token budget. The loop is deliberately small; judgment lives in
prompts and tools, not here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from presence.core.budget import BudgetExhausted, DailyBudget
from presence.core.providers import Provider, ProviderResponse, ToolSpec, Usage

TOOL_RESULT_MAX_CHARS = 8_000


@dataclass
class Tool:
    spec: ToolSpec
    handler: Callable[..., str]


class AgentSpec(BaseModel):
    name: str
    model: str
    system: str
    max_turns: int = 12
    max_output_tokens: int = 1024


class AgentResult(BaseModel):
    text: str = ""
    turns: int = 0
    stopped: Literal["done", "max_turns", "budget"] = "done"
    usage: Usage = Field(default_factory=Usage)
    transcript: list[dict[str, Any]] = Field(default_factory=list)


def _execute_tool(tools: dict[str, Tool], name: str, arguments: dict[str, Any]) -> str:
    tool = tools.get(name)
    if tool is None:
        return f"ERROR: unknown tool '{name}'"
    try:
        result = tool.handler(**arguments)
    except Exception as exc:  # a failing tool is data for the model, not a crash
        return f"ERROR: {type(exc).__name__}: {exc}"
    result = str(result)
    if len(result) > TOOL_RESULT_MAX_CHARS:
        result = result[:TOOL_RESULT_MAX_CHARS] + " …[truncated]"
    return result


def run_agent(
    spec: AgentSpec,
    task: str,
    tools: list[Tool],
    provider: Provider,
    budget: DailyBudget | None = None,
) -> AgentResult:
    """Run one agent to completion: model turns alternate with tool execution
    until the model answers without tool calls, a cap is hit, or the daily
    budget is exhausted (a clean stop, never an exception to the caller)."""
    by_name = {t.spec.name: t for t in tools}
    specs = [t.spec for t in tools]
    transcript: list[dict[str, Any]] = [{"role": "user", "content": task}]
    total = Usage()
    text = ""

    for turn in range(1, spec.max_turns + 1):
        if budget is not None:
            try:
                budget.ensure_available()
            except BudgetExhausted:
                return AgentResult(
                    text=text, turns=turn - 1, stopped="budget", usage=total, transcript=transcript
                )

        response: ProviderResponse = provider.complete(
            model=spec.model,
            system=spec.system,
            transcript=transcript,
            tools=specs,
            max_output_tokens=spec.max_output_tokens,
        )
        total.input_tokens += response.usage.input_tokens
        total.output_tokens += response.usage.output_tokens
        if budget is not None:
            budget.charge(response.usage)
        text = response.text or text

        if not response.tool_calls:
            return AgentResult(
                text=response.text, turns=turn, stopped="done", usage=total, transcript=transcript
            )

        transcript.append(
            {"role": "assistant", "content": response.text, "tool_calls": response.tool_calls}
        )
        for call in response.tool_calls:
            transcript.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": _execute_tool(by_name, call.name, call.arguments),
                }
            )

    return AgentResult(
        text=text, turns=spec.max_turns, stopped="max_turns", usage=total, transcript=transcript
    )


__all__ = ["AgentResult", "AgentSpec", "Tool", "run_agent"]
