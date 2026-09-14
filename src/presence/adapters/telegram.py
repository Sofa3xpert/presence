"""Telegram delivery via the Bot API — the customer's own bot, their own chat.

Pairing: the customer creates a bot with @BotFather (one token), messages it
once, and `presence telegram pair` reads that message to learn the chat id.
Nothing is stored beyond the token and chat id in the local secrets file."""

from __future__ import annotations

import time
from typing import Any

import requests

API = "https://api.telegram.org/bot{token}/{method}"
MAX_CHARS = 4000  # Telegram caps a message at 4096; leave headroom


class TelegramError(Exception):
    pass


def _call(token: str, method: str, **payload: Any) -> Any:
    try:
        r = requests.post(API.format(token=token, method=method), json=payload, timeout=25)
        data = r.json()
    except Exception as exc:
        raise TelegramError(f"{method}: {type(exc).__name__}: {str(exc)[:120]}") from exc
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', 'unknown error')}")
    return data["result"]


def chunks(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split on line boundaries so a long brief arrives as readable parts."""
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > limit and cur:
            out.append(cur.rstrip("\n"))
            cur = ""
        cur += line
    if cur.strip():
        out.append(cur.rstrip("\n"))
    return out or [""]


class TelegramMessenger:
    name = "telegram"

    def __init__(self, token: str, chat_id: int | str):
        if not token or not chat_id:
            raise TelegramError(
                "telegram needs both a bot token and a chat id — run: presence telegram pair")
        self.token, self.chat_id = token, int(chat_id)

    def send(self, text: str) -> None:
        for part in chunks(text):
            _call(self.token, "sendMessage", chat_id=self.chat_id, text=part,
                  disable_web_page_preview=True)


def latest_chat_id(updates: list[dict[str, Any]]) -> int | None:
    """The chat id of the most recent private message in a getUpdates result."""
    for u in reversed(updates):
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            return int(chat["id"])
    return None


def pair(token: str, wait_seconds: int = 90, poll: float = 3.0) -> int | None:
    """Wait for the customer to message their bot, then return the chat id."""
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        chat_id = latest_chat_id(_call(token, "getUpdates", timeout=0))
        if chat_id:
            return chat_id
        time.sleep(poll)
    return None
