"""One daily cycle — shared by the command line and the local app."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from presence.adapters import ConsoleMessenger, TelegramError, TelegramMessenger
from presence.agents.brief import compose_brief
from presence.connectors import SeenPostings, run_sources
from presence.core.config import load_profile, load_search, load_sources
from presence.core.secrets import get_secret
from presence.tracker import Tracker
from presence.tracker.conventions import link_key


def telegram_for(data: Path) -> TelegramMessenger | None:
    token, chat = get_secret("TELEGRAM_BOT_TOKEN", data), get_secret("TELEGRAM_CHAT_ID", data)
    return TelegramMessenger(token, chat) if token and chat else None


def run_cycle(data: Path, send: bool = False) -> tuple[str, dict[str, str], bool]:
    """Fetch → filter → tracker → brief. Returns (brief, source errors, delivered).
    Raises ConfigError for an unconfirmed profile or broken config (charter rule 2)."""
    load_profile(data)
    search, sources = load_search(data), load_sources(data)
    seen = SeenPostings(data / "seen.json")
    tracker = Tracker(data / "tracker.db")
    try:
        postings, errors = run_sources(sources, search, seen=seen.keys())
        new_jobs = [
            job for p in postings for job, created in [tracker.ingest(p.candidate)] if created
        ]
        seen.mark([link_key(p.url) for p in postings if p.url])
        brief = compose_brief(tracker, new_jobs, errors, today=date.today())
    finally:
        tracker.close()
    delivered = False
    if send:
        messenger = telegram_for(data)
        if messenger is not None:
            messenger.send(brief)
            delivered = True
    (data / "last_brief.txt").write_text(brief)
    return brief, errors, delivered


__all__ = ["ConsoleMessenger", "TelegramError", "run_cycle", "telegram_for"]
