"""M1 verification: run a trivial tool-using agent against a real provider.

    uv run python -m presence.core.check anthropic claude-haiku-4-5-20251001
    uv run python -m presence.core.check openai gpt-4o-mini
    uv run python -m presence.core.check ollama llama3.1:8b
    uv run python -m presence.core.check lmstudio <model>   # localhost:1234

Keys come from the environment: ANTHROPIC_API_KEY / OPENAI_API_KEY (local
servers need none). The check passes when the model calls the echo tool and
then finishes with a plain answer — proving the full loop: tool schema
translation, tool execution, result feedback, and final response parsing.
"""

from __future__ import annotations

import os
import sys

from presence.core.executor import AgentSpec, Tool, run_agent
from presence.core.providers import AnthropicProvider, OpenAICompatProvider, ToolSpec

PRESETS: dict[str, dict[str, str | None]] = {
    "anthropic": {"kind": "anthropic", "key_env": "ANTHROPIC_API_KEY", "base_url": None},
    "openai": {"kind": "openai-compatible", "key_env": "OPENAI_API_KEY", "base_url": None},
    "ollama": {
        "kind": "openai-compatible",
        "key_env": None,
        "base_url": "http://localhost:11434/v1",
    },
    "lmstudio": {
        "kind": "openai-compatible",
        "key_env": None,
        "base_url": "http://localhost:1234/v1",
    },
}


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in PRESETS:
        print(__doc__)
        return 2
    preset, model = PRESETS[sys.argv[1]], sys.argv[2]

    key = ""
    if preset["key_env"]:
        key = os.environ.get(str(preset["key_env"]), "")
        if not key:
            print(f"missing {preset['key_env']} in environment")
            return 2

    if preset["kind"] == "anthropic":
        provider = AnthropicProvider(api_key=key)
    else:
        provider = OpenAICompatProvider(api_key=key, base_url=preset["base_url"])  # type: ignore[arg-type]

    seen: list[str] = []
    echo = Tool(
        spec=ToolSpec(
            name="echo",
            description="Echo the given text back, verbatim.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        handler=lambda text: (seen.append(text), f"echoed: {text}")[1],
    )

    result = run_agent(
        AgentSpec(
            name="m1-check",
            model=model,
            system="You are a connectivity check. Follow the task exactly and briefly.",
            max_turns=4,
            max_output_tokens=200,
        ),
        task="Call the echo tool once with text='presence'. After you see its result, reply DONE.",
        tools=[echo],
        provider=provider,
    )

    tool_ok = "presence" in " ".join(seen).lower()
    print(f"stopped={result.stopped} turns={result.turns} "
          f"tokens={result.usage.total} tool_called={tool_ok}")
    print(f"final: {result.text.strip()[:200]}")
    if result.stopped == "done" and tool_ok:
        print("M1 CHECK PASS")
        return 0
    print("M1 CHECK FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
