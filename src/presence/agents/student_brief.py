"""Student Daily Brief — a rule-based morning digest of today's schedule.

Reads calendar events from .ics files and composes a plain-text brief
ready to display in the web UI or send via Telegram.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from presence.connectors.ical import (
    CalendarEvent,
    events_for_date,
    load_all_ics,
    upcoming_events,
)


def _format_event_line(e: CalendarEvent, number: int) -> str:
    """One line: '1. 09:15-11:00  Calculus II — Room 301'."""
    loc = f" — {e.location}" if e.location else ""
    return f"{number}. {e.format_time()}  {e.summary}{loc}"


def _section_for_day(events: list[CalendarEvent], label: str) -> list[str]:
    """A block of lines for a single day: header + numbered events."""
    if not events:
        return [f"{label}: free day, no classes."]
    lines = [f"{label} ({len(events)} class{'es' if len(events) != 1 else ''}):"]
    for i, e in enumerate(sorted(events, key=lambda ev: ev.start), 1):
        lines.append(f"  {_format_event_line(e, i)}")
    return lines


def compose_student_brief(
    cal_dir: Path,
    today: date | None = None,
    lookahead_days: int = 7,
) -> str:
    """Build the daily student brief text.

    Returns the full brief as a plain-text string.
    """
    today = today or date.today()
    tomorrow = today + timedelta(days=1)

    # --- load all calendar events ---
    if not cal_dir.is_dir():
        return (
            f"Student Presence \u00b7 {today:%a} {today.day} {today:%b}\n"
            "No calendar found. Place a .ics file in the calendars folder."
        )

    all_events = load_all_ics(cal_dir)
    if not all_events:
        return (
            f"Student Presence \u00b7 {today:%a} {today.day} {today:%b}\n"
            "Your calendar is empty \u2014 no events in any .ics file."
        )

    today_events = events_for_date(all_events, today)
    tomorrow_events = events_for_date(all_events, tomorrow)
    week_events = upcoming_events(all_events, days=lookahead_days, today=today)

    # --- header ---
    lines: list[str] = [f"Student Presence \u00b7 {today:%a} {today.day} {today:%b}"]
    lines.append("")

    # --- today ---
    lines.extend(_section_for_day(today_events, "Today"))
    if today_events:
        first = min(today_events, key=lambda e: e.start)
        lines.append(f"  First class at {first.start.strftime('%H:%M')}.")
    lines.append("")

    # --- tomorrow preview ---
    lines.extend(_section_for_day(tomorrow_events, f"Tomorrow ({tomorrow:%a})"))
    lines.append("")

    # --- week ahead (days after tomorrow that have events) ---
    later: list[str] = []
    for offset in range(2, lookahead_days):
        d = today + timedelta(days=offset)
        day_events = events_for_date(all_events, d)
        if day_events:
            count = len(day_events)
            later.append(
                f"  {d:%a} {d.day} {d:%b}: {count} class{'es' if count != 1 else ''}"
            )
    if later:
        lines.append("This week:")
        lines.extend(later)
    else:
        lines.append("Nothing else scheduled this week.")

    lines.append("")
    lines.append("Have a good day!")
    return "\n".join(lines)
