"""Opportunity Scout — finds internships, scholarships, events, mentorships.

Collects opportunities from multiple sources (job board connectors, RSS feeds,
manual entries), deduplicates, and composes a digest message.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel

log = logging.getLogger("presence.scout")

# --- Opportunity kinds (what the Scout looks for) ---
KINDS = ("internship", "scholarship", "event", "mentorship", "other")


class Opportunity(BaseModel):
    """One opportunity — an internship, scholarship, event, or mentorship."""

    title: str
    organization: str = ""          # company, university, or org name
    kind: str = "other"             # one of KINDS
    url: str = ""
    location: str = ""
    deadline: str = ""              # ISO date if known
    description: str = ""
    source: str = ""                # where we found it (e.g. "greenhouse", "rss:scholarships")
    found_date: str = ""            # ISO date when the Scout first saw it

    @property
    def key(self) -> str:
        """Unique key for deduplication."""
        if self.url:
            return self.url.strip().rstrip("/").lower()
        return f"{self.organization.lower()}|{self.title.lower()}"


class ScoutConfig(BaseModel):
    """User's preferences for the Opportunity Scout."""

    keywords: list[str] = ["intern", "graduate", "student", "trainee"]
    keywords_exclude: list[str] = []
    locations: list[str] = []
    kinds: list[str] = list(KINDS)   # which kinds to show
    rss_feeds: list[dict[str, str]] = []  # [{url: ..., label: ..., kind: ...}]
    max_results: int = 20


def load_scout_config(data: Path) -> ScoutConfig:
    """Load scout config from student_scout.json, or return defaults."""
    path = data / "student_scout.json"
    if path.exists():
        raw = json.loads(path.read_text())
        return ScoutConfig(**raw)
    return ScoutConfig()


def save_scout_config(data: Path, config: ScoutConfig) -> None:
    """Save scout config to student_scout.json."""
    path = data / "student_scout.json"
    path.write_text(json.dumps(config.model_dump(), indent=2))


# --- Seen opportunities (deduplication across runs) ---

class SeenOpportunities:
    """Track which opportunities we've already shown to the user."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, str] = {}  # key -> ISO date first seen
        if path.exists():
            self._data = json.loads(path.read_text())

    def is_seen(self, key: str) -> bool:
        return key in self._data

    def mark(self, keys: list[str], today: date | None = None) -> None:
        today_str = (today or date.today()).isoformat()
        for k in keys:
            if k not in self._data:
                self._data[k] = today_str
        self._path.write_text(json.dumps(self._data, indent=2))

    def keys(self) -> set[str]:
        return set(self._data)

    def cleanup(self, max_age_days: int = 90) -> None:
        """Remove entries older than max_age_days to keep the file small."""
        cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
        self._data = {k: v for k, v in self._data.items() if v >= cutoff}
        self._path.write_text(json.dumps(self._data, indent=2))


# --- Matching / filtering ---

def matches_scout(opp: Opportunity, config: ScoutConfig) -> bool:
    """Does this opportunity match the user's scout preferences?"""
    text = f"{opp.title} {opp.organization} {opp.description}".lower()

    # exclude keywords
    if config.keywords_exclude:
        if any(kw.lower() in text for kw in config.keywords_exclude):
            return False

    # include keywords (at least one must match)
    if config.keywords:
        if not any(kw.lower() in text for kw in config.keywords):
            return False

    # location filter
    if config.locations:
        loc = opp.location.lower()
        if not any(w.lower() in loc for w in config.locations):
            # also check description for remote/location mentions
            if not any(w.lower() in text for w in config.locations):
                return False

    # kind filter
    if opp.kind not in config.kinds:
        return False

    return True


# --- Compose the Scout digest ---

def _deadline_label(opp: Opportunity, today: date) -> str:
    """Human-readable deadline info, e.g. 'due in 5 days'."""
    if not opp.deadline:
        return ""
    try:
        dl = date.fromisoformat(opp.deadline[:10])
    except ValueError:
        return ""
    delta = (dl - today).days
    if delta < 0:
        return "(expired)"
    if delta == 0:
        return "(due TODAY)"
    if delta == 1:
        return "(due tomorrow)"
    return f"(due in {delta} days)"


def compose_scout_digest(
    opportunities: list[Opportunity],
    today: date | None = None,
    limit: int = 20,
) -> str:
    """Build the Opportunity Scout digest text."""
    today = today or date.today()

    lines = [f"Opportunity Scout \u00b7 {today:%a} {today.day} {today:%b}"]
    lines.append("")

    if not opportunities:
        lines.append("No new opportunities found today.")
        lines.append("Try adding more RSS feeds or adjusting your keywords.")
        lines.append("")
        lines.append("Keep looking!")
        return "\n".join(lines)

    # group by kind
    by_kind: dict[str, list[Opportunity]] = {}
    for opp in opportunities[:limit]:
        by_kind.setdefault(opp.kind, []).append(opp)

    # kind labels for display (более понятные названия)
    kind_labels = {
        "internship": "Internships",
        "scholarship": "Scholarships",
        "event": "Events & Career Fairs",
        "mentorship": "Mentorships",
        "other": "Other Opportunities",
    }

    total = len(opportunities)
    lines.append(f"{total} new opportunit{'y' if total == 1 else 'ies'} found:")
    lines.append("")

    for kind in KINDS:
        opps = by_kind.get(kind, [])
        if not opps:
            continue
        lines.append(f"{kind_labels.get(kind, kind)} ({len(opps)}):")
        for i, opp in enumerate(opps, 1):
            org = f"{opp.organization}: " if opp.organization else ""
            loc = f" — {opp.location}" if opp.location else ""
            dl = _deadline_label(opp, today)
            lines.append(f"  {i}. {org}{opp.title}{loc}")
            if dl:
                lines.append(f"     {dl}")
            if opp.url:
                lines.append(f"     {opp.url}")
        lines.append("")

    if len(opportunities) > limit:
        lines.append(f"...and {len(opportunities) - limit} more.")
        lines.append("")

    lines.append("Good luck!")
    return "\n".join(lines)
