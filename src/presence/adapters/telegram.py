"""Telegram delivery via the Bot API — the customer's own bot, their own chat.

Pairing: the customer creates a bot with @BotFather (one token). The app then
shows a deep link / QR code, https://t.me/<bot>?start=<code>; pressing Start
sends "/start <code>" and only the chat carrying that code is paired (Telegram's
documented deep-linking, https://core.telegram.org/bots/features#deep-linking).
`presence telegram pair` keeps the plain fallback: the latest message wins.
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
        text = str(exc).replace(token, "<token>")  # the URL in a network error holds the token
        raise TelegramError(f"{method}: {type(exc).__name__}: {text[:120]}") from exc
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


def get_me(token: str) -> dict[str, str]:
    """The bot's own identity; its username is what the pairing link needs."""
    me = _call(token, "getMe")
    return {"username": me.get("username", ""), "first_name": me.get("first_name", "")}


def pairing_link(username: str, code: str) -> str:
    return f"https://t.me/{username}?start={code}"


def find_start(updates: list[dict[str, Any]], code: str) -> dict[str, Any] | None:
    """The private chat that pressed Start on our link: its text is '/start <code>'."""
    for u in reversed(updates):
        msg = u.get("message") or {}
        chat = msg.get("chat") or {}
        if (msg.get("text") or "").strip() == f"/start {code}" and chat.get("id"):
            who = msg.get("from") or {}
            return {"chat_id": int(chat["id"]), "name": who.get("first_name", ""),
                    "update_id": u.get("update_id", 0)}
    return None


def _ack(token: str, update_id: int) -> None:
    """Confirm updates up to update_id so the pairing message is not seen twice."""
    try:
        _call(token, "getUpdates", offset=update_id + 1, timeout=0)
    except TelegramError:
        pass


def pair_once(token: str, code: str) -> dict[str, Any] | None:
    """One look at pending updates for our code; the app polls this."""
    hit = find_start(_call(token, "getUpdates", timeout=0), code)
    if hit:
        _ack(token, hit["update_id"])
    return hit


def pair(token: str, wait_seconds: int = 90, poll: float = 3.0,
         code: str | None = None) -> int | None:
    """Wait for the customer to message their bot, then return the chat id.
    With a code only the chat that used the pairing link counts."""
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if code:
            hit = pair_once(token, code)
            if hit:
                return hit["chat_id"]
        else:
            chat_id = latest_chat_id(_call(token, "getUpdates", timeout=0))
            if chat_id:
                return chat_id
        time.sleep(poll)
    return None
