"""iCal connector — reads .ics files (class timetables, exams, deadlines).

Parses the standard iCalendar format (RFC 5545) without external dependencies.
A university timetable exported from Google Calendar, Outlook, or a uni portal
is typically one .ics file with many VEVENT blocks.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class CalendarEvent(BaseModel):
    """One calendar event — a class, exam, or deadline."""

    summary: str
    start: datetime
    end: datetime | None = None
    location: str = ""
    description: str = ""
    source_file: str = ""

    @property
    def date(self) -> date:
        return self.start.date()

    @property
    def duration_minutes(self) -> int | None:
        if self.end is None:
            return None
        return int((self.end - self.start).total_seconds() / 60)

    def format_time(self) -> str:
        """Human-readable time range, e.g. '09:15–11:00'."""
        s = self.start.strftime("%H:%M")
        if self.end:
            return f"{s}–{self.end.strftime('%H:%M')}"
        return s

    def __str__(self) -> str:
        parts = [self.format_time(), self.summary]
        if self.location:
            parts.append(f"— {self.location}")
        return " ".join(parts)


def _unfold(text: str) -> str:
    """RFC 5545: long lines are folded with CRLF + space/tab."""
    return text.replace("\r\n ", "").replace("\r\n\t", "").replace("\n ", "").replace("\n\t", "")


def _unescape(value: str) -> str:
    """Unescape iCal text values."""
    return (value
            .replace("\\n", "\n")
            .replace("\\N", "\n")
            .replace("\\,", ",")
            .replace("\\;", ";")
            .replace("\\\\", "\\"))


def _parse_dt(value: str) -> datetime:
    """Parse an iCal datetime (DTSTART/DTEND).

    Handles: 20261001T091500, 20261001T091500Z, 20261001 (all-day).
    Timezone parameters (TZID=...) are stripped — we use naive local time.
    """
    # strip any parameters like TZID=Europe/Moscow:20261001T091500
    if ":" in value:
        value = value.rsplit(":", 1)[-1]
    value = value.strip().rstrip("Z")
    if len(value) == 8:  # all-day: 20261001
        return datetime.strptime(value, "%Y%m%d")
    return datetime.strptime(value, "%Y%m%dT%H%M%S")


def _parse_rrule(value: str) -> tuple[str, int]:
    """Parse a simple RRULE into (frequency, count). Only handles WEEKLY+COUNT."""
    parts = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
    freq = parts.get("FREQ", "")
    count = int(parts.get("COUNT", "1"))
    return freq, count


def _expand_recurring(event: CalendarEvent, freq: str, count: int) -> list[CalendarEvent]:
    """Expand a recurring event into individual occurrences."""
    if freq != "WEEKLY" or count <= 1:
        return [event]
    delta = timedelta(weeks=1)
    duration = (event.end - event.start) if event.end else None
    occurrences: list[CalendarEvent] = []
    for i in range(count):
        new_start = event.start + delta * i
        new_end = (new_start + duration) if duration else None
        occurrences.append(CalendarEvent(
            summary=event.summary,
            start=new_start,
            end=new_end,
            location=event.location,
            description=event.description,
            source_file=event.source_file,
        ))
    return occurrences


def parse_ics(text: str, source_file: str = "") -> list[CalendarEvent]:
    """Parse an .ics file's text content into CalendarEvent objects."""
    text = _unfold(text)
    events: list[CalendarEvent] = []
    in_event = False
    props: dict[str, str] = {}

    for line in text.splitlines():
        line = line.strip()
        if line == "BEGIN:VEVENT":
            in_event = True
            props = {}
        elif line == "END:VEVENT" and in_event:
            in_event = False
            summary = _unescape(props.get("SUMMARY", "")).strip()
            if not summary or "DTSTART" not in props:
                continue
            start = _parse_dt(props["DTSTART"])
            end = _parse_dt(props["DTEND"]) if "DTEND" in props else None
            event = CalendarEvent(
                summary=summary,
                start=start,
                end=end,
                location=_unescape(props.get("LOCATION", "")).strip(),
                description=_unescape(props.get("DESCRIPTION", "")).strip(),
                source_file=source_file,
            )
            if "RRULE" in props:
                freq, count = _parse_rrule(props["RRULE"])
                events.extend(_expand_recurring(event, freq, count))
            else:
                events.append(event)
        elif in_event and ":" in line:
            # handle properties with parameters: DTSTART;TZID=Europe/Moscow:20261001T091500
            key_part, _, val = line.partition(":")
            # the key is before any semicolon (parameters come after ;)
            key = key_part.split(";")[0].upper()
            if key in ("SUMMARY", "DTSTART", "DTEND", "LOCATION", "DESCRIPTION", "RRULE"):
                # for DTSTART/DTEND, keep parameters in value for _parse_dt
                if key in ("DTSTART", "DTEND") and ";" in key_part:
                    val = key_part.split(";", 1)[1] + ":" + val
                props[key] = val

    return events


def load_ics(path: Path) -> list[CalendarEvent]:
    """Load and parse a single .ics file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_ics(text, source_file=path.name)


def load_all_ics(directory: Path) -> list[CalendarEvent]:
    """Load all .ics files from a directory, sorted by start time."""
    events: list[CalendarEvent] = []
    for f in sorted(directory.glob("*.ics")):
        events.extend(load_ics(f))
    events.sort(key=lambda e: e.start)
    return events


def events_for_date(events: list[CalendarEvent], target: date) -> list[CalendarEvent]:
    """Filter events for a specific date."""
    return [e for e in events if e.date == target]


def upcoming_events(events: list[CalendarEvent], days: int = 7,
                    today: date | None = None) -> list[CalendarEvent]:
    """Events in the next N days (including today)."""
    start = today or date.today()
    end = start + timedelta(days=days)
    return [e for e in events if start <= e.date < end]
