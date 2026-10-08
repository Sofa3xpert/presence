"""One daily cycle — shared by the command line and the local app."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from presence.adapters import ConsoleMessenger, TelegramError, TelegramMessenger
from presence.agents.brief import compose_brief
from presence.connectors import Posting, SeenPostings, fetch_description, run_sources
from presence.core.config import SourceEntry, load_profile, load_search, load_sources
from presence.core.secrets import get_secret
from presence.tracker import Tracker
from presence.tracker.conventions import link_key

log = logging.getLogger("presence.cycle")


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


def run_student_brief(data: Path, send: bool = False) -> tuple[str, bool]:
    """Compose the student daily brief and optionally send via Telegram.

    Returns (brief_text, delivered).
    """
    from presence.agents.student_brief import compose_student_brief
    from presence.connectors.cal_feeds import refresh_all

    # refresh URL-based calendars (Canvas, etc.) before composing
    refresh_all(data)

    cal_dir = data / "calendars"

    # scholarship deadline reminders
    from presence.agents.scholar_tracker import ScholarshipTracker, scholarship_brief_section
    scholar_tracker = ScholarshipTracker(data / "scholarships.json")
    scholarship_lines = scholarship_brief_section(scholar_tracker)

    brief = compose_student_brief(cal_dir, scholarship_lines=scholarship_lines)
    (data / "last_student_brief.txt").write_text(brief)
    delivered = False
    if send:
        messenger = telegram_for(data)
        if messenger is not None:
            messenger.send(brief)
            delivered = True
    return brief, delivered


def _postings_to_opportunities(data: Path, config: Any) -> list[Any]:
    """Reuse existing job board connectors to find internships.

    Takes the user's configured sources (Greenhouse, Lever, etc.) and converts
    Postings that match scout keywords into Opportunity objects.
    """
    from presence.agents.scout import Opportunity
    from presence.core.config import load_sources

    try:
        sources = load_sources(data)
    except Exception:
        return []

    if not sources:
        return []

    from presence.connectors.base import ConnectorError, create

    opportunities: list[Any] = []
    for src in sources:
        if not src.enabled:
            continue
        try:
            conn = create(src.provider)
            postings = conn.fetch(src)
        except ConnectorError as exc:
            log.warning("Scout: board source %s failed: %s", src.id, exc)
            continue
        for p in postings:
            # only keep postings that look like student opportunities
            text = f"{p.title} {p.description}".lower()
            if any(kw.lower() in text for kw in config.keywords):
                opportunities.append(Opportunity(
                    title=p.title,
                    organization=p.company,
                    kind="internship",
                    url=p.url,
                    location=p.location,
                    source=p.source,
                    found_date=date.today().isoformat(),
                ))
    return opportunities


def run_student_scout(data: Path) -> int:
    """Run the Opportunity Scout: fetch from RSS + job boards, add new items to tracker.

    Returns the number of new opportunities added.
    """
    from presence.agents.scholar_tracker import ScholarshipTracker
    from presence.agents.scout import (
        SeenOpportunities,
        load_scout_config,
        matches_scout,
    )
    from presence.connectors.rss import fetch_all_feeds

    config = load_scout_config(data)
    seen = SeenOpportunities(data / "seen_opportunities.json")
    tracker = ScholarshipTracker(data / "scholarships.json")

    # 1. Fetch from RSS feeds
    rss_opps = fetch_all_feeds(config.rss_feeds)

    # 2. Fetch from job board connectors (internships)
    board_opps = _postings_to_opportunities(data, config)

    # 3. Combine, dedupe, filter, add to tracker
    all_opps = rss_opps + board_opps
    added = 0
    seen_keys: set[str] = set()
    for opp in all_opps:
        key = opp.key
        if key in seen_keys or seen.is_seen(key):
            continue
        if not matches_scout(opp, config):
            continue
        # skip if already in tracker (by URL)
        if tracker.has_url(opp.url):
            seen_keys.add(key)
            continue
        seen_keys.add(key)
        tracker.add(
            name=opp.title,
            organization=opp.organization,
            kind=opp.kind,
            url=opp.url,
            deadline=opp.deadline,
            notes=opp.description[:200] if opp.description else "",
            source=opp.source,
        )
        added += 1

    # Mark all as seen so we don't re-add next run
    if seen_keys:
        seen.mark(list(seen_keys))

    return added


__all__ = ["ConsoleMessenger", "TelegramError", "keep_description", "run_cycle",
           "run_student_brief", "run_student_scout",
           "sheet_sync_if_configured", "telegram_for"]
