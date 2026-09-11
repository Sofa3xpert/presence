"""Pure translation-layer tests: neutral transcript -> provider payloads."""

from presence.core.providers import (
    ToolCall,
    ToolSpec,
    anthropic_tools,
    openai_tools,
    to_anthropic_messages,
    to_openai_messages,
)

TOOLS = [ToolSpec(name="echo", description="d", parameters={"type": "object"})]

TRANSCRIPT = [
    {"role": "user", "content": "task"},
    {
        "role": "assistant",
        "content": "thinking",
        "tool_calls": [ToolCall(id="c1", name="echo", arguments={"text": "hi"})],
    },
    {"role": "tool", "tool_call_id": "c1", "name": "echo", "content": "echoed:hi"},
    {"role": "tool", "tool_call_id": "c2", "name": "echo", "content": "second"},
]


def test_tool_schema_translation():
    assert anthropic_tools(TOOLS)[0]["input_schema"] == {"type": "object"}
    assert openai_tools(TOOLS)[0]["function"]["name"] == "echo"


def test_anthropic_messages_merge_tool_results_into_one_user_turn():
    messages = to_anthropic_messages(TRANSCRIPT)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assistant = messages[1]["content"]
    assert {"type": "tool_use", "id": "c1", "name": "echo", "input": {"text": "hi"}} in assistant
    results = messages[2]["content"]
    assert len(results) == 2
    assert results[0]["tool_use_id"] == "c1"
    assert results[1]["content"] == "second"


def test_openai_messages_shape():
    messages = to_openai_messages("sys", TRANSCRIPT)
    assert messages[0] == {"role": "system", "content": "sys"}
    assistant = messages[2]
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"text": "hi"}'
    assert messages[3] == {"role": "tool", "tool_call_id": "c1", "content": "echoed:hi"}
