"""Messenger transports. v1: Telegram (plus a console stand-in)."""

from presence.adapters.base import ConsoleMessenger, Messenger
from presence.adapters.telegram import (
    TelegramError,
    TelegramMessenger,
    chunks,
    get_me,
    pair,
    pair_once,
    pairing_link,
)

__all__ = ["ConsoleMessenger", "Messenger", "TelegramError", "TelegramMessenger", "chunks",
           "get_me", "pair", "pair_once", "pairing_link"]
