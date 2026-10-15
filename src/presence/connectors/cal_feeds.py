"""Calendar feed manager — stores URLs and refreshes .ics files on demand.

When a student adds a calendar via URL (like a Canvas feed), we save the
URL so we can re-fetch it before each daily brief to pick up new assignments.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("presence.cal_feeds")

FEEDS_FILE = "calendar_feeds.json"


def _load_feeds(data: Path) -> list[dict]:
    path = data / FEEDS_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def _save_feeds(data: Path, feeds: list[dict]) -> None:
    (data / FEEDS_FILE).write_text(json.dumps(feeds, indent=2))


def add_feed(data: Path, url: str, filename: str, label: str = "") -> None:
    """Register a calendar URL for auto-refresh."""
    feeds = _load_feeds(data)
    # avoid duplicates
    if any(f["url"] == url for f in feeds):
        return
    feeds.append({"url": url, "filename": filename, "label": label or filename})
    _save_feeds(data, feeds)


def remove_feed(data: Path, url: str) -> None:
    """Remove a feed by URL."""
    feeds = _load_feeds(data)
    feeds = [f for f in feeds if f["url"] != url]
    _save_feeds(data, feeds)


def list_feeds(data: Path) -> list[dict]:
    """Return all registered feeds."""
    return _load_feeds(data)


def refresh_all(data: Path) -> list[str]:
    """Re-fetch all registered feed URLs and overwrite their .ics files.

    Returns list of error messages (empty = all OK).
    """
    feeds = _load_feeds(data)
    cal_dir = data / "calendars"
    cal_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    for feed in feeds:
        url = feed["url"]
        filename = feed["filename"]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Presence/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            if "BEGIN:VCALENDAR" not in text:
                errors.append(f"{filename}: feed returned invalid data")
                continue
            (cal_dir / filename).write_text(text, encoding="utf-8")
            log.info("Refreshed %s from %s", filename, url[:60])
        except Exception as exc:
            errors.append(f"{filename}: {exc}")
            log.warning("Failed to refresh %s: %s", filename, exc)

    return errors
