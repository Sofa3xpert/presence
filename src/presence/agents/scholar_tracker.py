"""Opportunity Tracker — track scholarships, internships, events you're applying to.

A simple JSON-backed list of opportunities with deadlines and statuses.
Items come from two places: added manually by the student, or found
automatically by the Scout (RSS feeds, job boards).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

STATUSES = ("to_apply", "applied", "won", "rejected", "expired")


def _parse_deadline_date(deadline: str) -> date | None:
    """Extract the date part from a deadline like '2026-10-08' or '2026-10-08 16:00'."""
    try:
        return date.fromisoformat(deadline.split()[0])
    except (ValueError, IndexError):
        return None
OPEN_STATUSES = ("to_apply", "applied")


KINDS = ("scholarship", "internship", "event", "mentorship", "other")


class Scholarship(BaseModel):
    """One opportunity the student is tracking."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    organization: str = "PolyU"
    kind: str = "scholarship"   # one of KINDS
    deadline: str = ""          # ISO date
    amount: str = ""            # e.g. "HK$10,000" — free text
    status: str = "to_apply"    # one of STATUSES
    notes: str = ""
    url: str = ""
    source: str = "manual"      # "manual" or "rss:..." or "greenhouse" etc.
    added: str = Field(default_factory=lambda: date.today().isoformat())


class ScholarshipTracker:
    """Read/write a list of scholarships from a JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._items: list[Scholarship] = []
        if path.exists():
            raw = json.loads(path.read_text())
            self._items = [Scholarship(**s) for s in raw]

    def _save(self) -> None:
        self._path.write_text(json.dumps(
            [s.model_dump() for s in self._items], indent=2,
        ))

    def all(self) -> list[Scholarship]:
        return list(self._items)

    def open(self) -> list[Scholarship]:
        return [s for s in self._items if s.status in OPEN_STATUSES]

    def get(self, scholarship_id: str) -> Scholarship | None:
        for s in self._items:
            if s.id == scholarship_id:
                return s
        return None

    def has_url(self, url: str) -> bool:
        """Check if an opportunity with this URL already exists."""
        if not url:
            return False
        normalized = url.strip().rstrip("/").lower()
        return any(
            s.url.strip().rstrip("/").lower() == normalized
            for s in self._items if s.url
        )

    def add(self, **kwargs) -> Scholarship:
        s = Scholarship(**kwargs)
        self._items.append(s)
        self._save()
        return s

    def update(self, scholarship_id: str, **kwargs) -> Scholarship | None:
        s = self.get(scholarship_id)
        if s is None:
            return None
        for k, v in kwargs.items():
            if hasattr(s, k) and k != "id":
                setattr(s, k, v)
        self._save()
        return s

    def remove(self, scholarship_id: str) -> bool:
        before = len(self._items)
        self._items = [s for s in self._items if s.id != scholarship_id]
        if len(self._items) < before:
            self._save()
            return True
        return False

    def upcoming_deadlines(self, days: int = 14, today: date | None = None) -> list[Scholarship]:
        """Scholarships with deadlines in the next N days, sorted by deadline."""
        today = today or date.today()
        cutoff = today + timedelta(days=days)
        result = []
        for s in self._items:
            if s.status != "to_apply" or not s.deadline:
                continue
            dl = _parse_deadline_date(s.deadline)
            if dl is None:
                continue
            if today <= dl <= cutoff:
                result.append(s)
        return sorted(result, key=lambda s: s.deadline)

    def expired_check(self, today: date | None = None) -> int:
        """Mark scholarships past deadline as expired. Returns count updated."""
        today = today or date.today()
        count = 0
        for s in self._items:
            if s.status != "to_apply" or not s.deadline:
                continue
            dl = _parse_deadline_date(s.deadline)
            if dl is None:
                continue
            if dl < today:
                s.status = "expired"
                count += 1
        if count:
            self._save()
        return count


def scholarship_brief_section(tracker: ScholarshipTracker, today: date | None = None) -> list[str]:
    """Build lines for the Daily Brief showing upcoming scholarship deadlines."""
    today = today or date.today()
    tracker.expired_check(today)
    upcoming = tracker.upcoming_deadlines(days=14, today=today)
    if not upcoming:
        return []

    lines = [f"Opportunity deadlines ({len(upcoming)}):"]
    for s in upcoming:
        dl = _parse_deadline_date(s.deadline)
        if dl is None:
            continue
        delta = (dl - today).days
        # extract time if present (e.g. "2026-10-08 16:00")
        time_str = ""
        if " " in s.deadline:
            time_str = f" at {s.deadline.split()[1]}"
        if delta == 0:
            urgency = f"TODAY{time_str}"
        elif delta == 1:
            urgency = f"tomorrow{time_str}"
        else:
            urgency = f"in {delta} days ({dl:%a} {dl.day} {dl:%b}){time_str}"
        lines.append(f"  - {s.name} — {urgency}")
    return lines
