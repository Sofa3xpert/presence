"""One daily cycle — shared by the command line and the local app."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from presence.adapters import ConsoleMessenger, TelegramError, TelegramMessenger
from presence.agents.brief import compose_brief
from presence.connectors import Posting, SeenPostings, fetch_description, run_sources
from presence.core.config import SourceEntry, load_profile, load_search, load_sources
from presence.core.secrets import get_secret
from presence.tracker import Tracker
from presence.tracker.conventions import link_key


def telegram_for(data: Path) -> TelegramMessenger | None:
    token, chat = get_secret("TELEGRAM_BOT_TOKEN", data), get_secret("TELEGRAM_CHAT_ID", data)
    return TelegramMessenger(token, chat) if token and chat else None


def sheet_sync_if_configured(data: Path, tracker: Tracker) -> str | None:
    """Mirror the tracker to the chosen Google Sheet. Returns an error text, or None."""
    cfg_path = data / "presence.yaml"
    cfg = (yaml.safe_load(cfg_path.read_text()) or {}) if cfg_path.exists() else {}
    tr = cfg.get("tracker") or {}
    if tr.get("backend") != "sheet" or not tr.get("sheet_id"):
        return None
    from presence.adapters import gsheet  # lazy: google-auth only when a sheet is used

    creds = gsheet.credentials(data)
    if creds is None:
        return "a Google Sheet is chosen but Google is not connected"
    try:
        client = gsheet.SheetClient(creds, data)
        gsheet.sync(tracker, client, tr["sheet_id"], tr.get("tab") or "Tracker",
                    data / gsheet.STATE_FILE)
    except gsheet.SheetError as exc:
        return str(exc)
    return None


def keep_description(data: Path, job_id: int, p: Posting, sources: list[SourceEntry],
                     errors: dict[str, str]) -> None:
    """Keep a new job's text beside the tracker as jd/<id>.txt. A board that
    lists jobs without their text gets one extra GET here, for this job only;
    a failure is noted under the source's label and never stops the cycle.
    Text the person already has for the job is never replaced."""
    from presence.app import jd  # lazy: the app package imports this module

    if jd.has(data, job_id):
        return
    text = p.description
    if not text and p.description_ref:
        try:
            text = fetch_description(p, sources)
        except Exception as exc:  # the job is tracked either way; only its text is missing
            errors[p.company] = f"job text not fetched: {exc}"
            return
    if text:
        jd.save(data, job_id, text)


def run_cycle(data: Path, send: bool = False) -> tuple[str, dict[str, str], bool]:
    """Fetch → filter → tracker → brief. Returns (brief, source errors, delivered).
    Raises ConfigError for an unconfirmed profile or broken config (charter rule 2)."""
    load_profile(data)
    search, sources = load_search(data), load_sources(data)
    seen = SeenPostings(data / "seen.json")
    tracker = Tracker(data / "tracker.db")
    try:
        postings, errors = run_sources(sources, search, seen=seen.keys())
        new_jobs = []
        for p in postings:
            job, created = tracker.ingest(p.candidate)
            if created:
                new_jobs.append(job)
                keep_description(data, job.id, p, sources, errors)
        seen.mark([link_key(p.url) for p in postings if p.url])
        sheet_error = sheet_sync_if_configured(data, tracker)
        if sheet_error:
            errors["google sheet"] = sheet_error
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


__all__ = ["ConsoleMessenger", "TelegramError", "keep_description", "run_cycle",
           "sheet_sync_if_configured", "telegram_for"]
