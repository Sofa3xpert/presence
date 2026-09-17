"""A stand-in for the OpenAI client: records every call, answers what it is told.
No network. ``install`` swaps it in for the duration of a test."""

from types import SimpleNamespace

import openai


class Status(Exception):
    """What the real client raises on an HTTP error: a message and a status code."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def reply(content=None, prompt_tokens=7, completion_tokens=3, **extra):
    """One chat completion whose message has ``content`` plus any extra fields
    (reasoning_content, reasoning, thinking, tool_calls...)."""
    msg = SimpleNamespace(content=content, **extra)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


class _Client:
    def __init__(self, seen, replies):
        self.seen, self.replies = seen, replies
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.seen.setdefault("calls", []).append(kwargs)
        out = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(out, Exception):
            raise out
        return out


def install(monkeypatch, *replies):
    """Make ``openai.OpenAI`` return a client that answers ``replies`` in turn
    (the last one repeats). Returns the dict the client records into:
    api_key, base_url and every ``create`` call's keyword arguments."""
    seen: dict = {}

    def fake_openai(api_key=None, base_url=None, **_):
        seen.update(api_key=api_key, base_url=base_url)
        return _Client(seen, list(replies))

    monkeypatch.setattr(openai, "OpenAI", fake_openai)
    return seen
