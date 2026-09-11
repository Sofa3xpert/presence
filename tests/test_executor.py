"""Executor loop tests against a scripted fake provider — no network."""

from presence.core.budget import DailyBudget
from presence.core.executor import AgentSpec, Tool, run_agent
from presence.core.providers import ProviderResponse, ToolCall, ToolSpec, Usage

SPEC = AgentSpec(name="t", model="fake", system="sys", max_turns=3, max_output_tokens=100)

ECHO = Tool(
    spec=ToolSpec(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
    ),
    handler=lambda text: f"echoed:{text}",
)

BOOM = Tool(
    spec=ToolSpec(name="boom", description="fails", parameters={"type": "object"}),
    handler=lambda **_: (_ for _ in ()).throw(RuntimeError("kaput")),
)


class FakeProvider:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self.script.pop(0)


def usage(n=10):
    return Usage(input_tokens=n, output_tokens=n)


def test_tool_roundtrip_then_done():
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[ToolCall(id="1", name="echo", arguments={"text": "hi"})],
                usage=usage(),
            ),
            ProviderResponse(text="DONE", stop_reason="end_turn", usage=usage()),
        ]
    )
    result = run_agent(SPEC, "task", [ECHO], provider)
    assert result.stopped == "done"
    assert result.text == "DONE"
    assert result.turns == 2
    assert result.usage.total == 40
    # the tool result reached the second call's transcript
    tool_entries = [e for e in provider.calls[1]["transcript"] if e["role"] == "tool"]
    assert tool_entries == [
        {"role": "tool", "tool_call_id": "1", "name": "echo", "content": "echoed:hi"}
    ]


def test_failing_and_unknown_tools_become_error_results():
    provider = FakeProvider(
        [
            ProviderResponse(
                tool_calls=[
                    ToolCall(id="1", name="boom", arguments={}),
                    ToolCall(id="2", name="ghost", arguments={}),
                ],
                usage=usage(),
            ),
            ProviderResponse(text="ok", usage=usage()),
        ]
    )
    result = run_agent(SPEC, "task", [BOOM], provider)
    assert result.stopped == "done"
    contents = [e["content"] for e in provider.calls[1]["transcript"] if e["role"] == "tool"]
    assert contents[0].startswith("ERROR: RuntimeError: kaput")
    assert contents[1] == "ERROR: unknown tool 'ghost'"


def test_max_turns_cap():
    loop = ProviderResponse(
        tool_calls=[ToolCall(id="1", name="echo", arguments={"text": "x"})], usage=usage()
    )
    provider = FakeProvider([loop, loop, loop])
    result = run_agent(SPEC, "task", [ECHO], provider)
    assert result.stopped == "max_turns"
    assert result.turns == 3


def test_budget_stops_cleanly(tmp_path):
    budget = DailyBudget(30, tmp_path / "ledger.json")
    loop = ProviderResponse(
        tool_calls=[ToolCall(id="1", name="echo", arguments={"text": "x"})], usage=usage()
    )
    provider = FakeProvider([loop, loop, loop])
    result = run_agent(SPEC, "task", [ECHO], provider, budget=budget)
    # first call charges 20, second charges 20 -> 40 >= 30, third never happens
    assert result.stopped == "budget"
    assert len(provider.calls) == 2
