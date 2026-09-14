"""Messenger transports behind one shape. v1 ships Telegram; a console
messenger stands in for tests and dry runs."""

from __future__ import annotations

from typing import Protocol


class Messenger(Protocol):
    name: str

    def send(self, text: str) -> None: ...


class ConsoleMessenger:
    name = "console"

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)
        print(text)
