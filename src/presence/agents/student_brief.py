"""Student Daily Brief — a rule-based morning digest of today's schedule.

Reads calendar events from .ics files and emails from IMAP, then composes
a plain-text brief ready to display in the web UI or send via Telegram.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from presence.connectors.ical import (
    CalendarEvent,
    deadlines_in_range,
    events_for_date,
    load_all_ics,
)

log = logging.getLogger("presence.student_brief")


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


def _deadline_section(deadlines: list[CalendarEvent], today: date) -> list[str]:
    """Build the upcoming-deadlines section of the brief."""
    if not deadlines:
        return []
    lines = [f"Upcoming deadlines ({len(deadlines)}):"]
    for i, d in enumerate(deadlines, 1):
        delta = (d.date - today).days
        if delta == 0:
            urgency = "TODAY"
        elif delta == 1:
            urgency = "tomorrow"
        else:
            urgency = f"in {delta} days ({d.start:%a} {d.start.day} {d.start:%b})"
        time_str = d.start.strftime("%H:%M") if d.start.hour or d.start.minute else ""
        at_part = f" at {time_str}" if time_str else ""
        lines.append(f"  {i}. {d.summary} — {urgency}{at_part}")
    return lines


def compose_student_brief(
    cal_dir: Path,
    today: date | None = None,
    lookahead_days: int = 7,
    scholarship_lines: list[str] | None = None,
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

    # Separate deadlines from classes
    deadlines = deadlines_in_range(all_events, days=lookahead_days, today=today)
    deadline_set = set(id(d) for d in deadlines)

    today_events = [e for e in events_for_date(all_events, today)
                    if id(e) not in deadline_set]
    tomorrow_events = [e for e in events_for_date(all_events, tomorrow)
                       if id(e) not in deadline_set]

    # --- header ---
    lines: list[str] = [f"Student Presence \u00b7 {today:%a} {today.day} {today:%b}"]
    lines.append("")

    # --- today ---
    lines.extend(_section_for_day(today_events, "Today"))
    if today_events:
        first = min(today_events, key=lambda e: e.start)
        lines.append(f"  First class at {first.start.strftime('%H:%M')}.")
    lines.append("")

    # --- deadlines ---
    dl_lines = _deadline_section(deadlines, today)
    if dl_lines:
        lines.extend(dl_lines)
        lines.append("")

    # --- tomorrow preview ---
    lines.extend(_section_for_day(tomorrow_events, f"Tomorrow ({tomorrow:%a})"))
    lines.append("")

    # --- week ahead (days after tomorrow that have events) ---
    later: list[str] = []
    for offset in range(2, lookahead_days):
        d = today + timedelta(days=offset)
        day_events = [e for e in events_for_date(all_events, d)
                      if id(e) not in deadline_set]
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

    # --- scholarship deadlines ---
    if scholarship_lines:
        lines.append("")
        lines.extend(scholarship_lines)

    lines.append("")
    lines.append("Have a good day!")
    return "\n".join(lines)


def compose_weekly_summary(
    cal_dir: Path,
    today: date | None = None,
    scholarship_lines: list[str] | None = None,
) -> str:
    """Build a weekly summary text (intended for Friday evenings).

    Looks BACK at Mon-Fri of the current week and summarises attendance,
    then looks FORWARD at next week with a per-day preview.
    Returns the full summary as a plain-text string.
    """
    today = today or date.today()

    # --- header ---
    lines: list[str] = [f"Weekly Summary \u00b7 Fri {today.day} {today:%b}"]
    lines.append("")

    # --- load calendar ---
    if not cal_dir.is_dir():
        lines.append("No calendar found. Place a .ics file in the calendars folder.")
        lines.append("")
        lines.append("Have a great weekend!")
        return "\n".join(lines)

    all_events = load_all_ics(cal_dir)
    if not all_events:
        lines.append("Your calendar is empty \u2014 no events in any .ics file.")
        lines.append("")
        lines.append("Have a great weekend!")
        return "\n".join(lines)

    # Separate deadlines from classes
    deadline_events = deadlines_in_range(all_events, days=14, today=today)
    deadline_set = set(id(d) for d in deadline_events)

    # --- THIS WEEK REVIEW (Mon-Fri) ---
    # Find Monday of the current week
    monday = today - timedelta(days=today.weekday())
    week_classes: list[CalendarEvent] = []
    subjects: set[str] = set()
    for offset in range(5):  # Mon-Fri
        d = monday + timedelta(days=offset)
        day_events = [e for e in events_for_date(all_events, d)
                      if id(e) not in deadline_set]
        week_classes.extend(day_events)
        for e in day_events:
            subjects.add(e.summary)

    lines.append("This week:")
    if week_classes:
        n = len(week_classes)
        lines.append(f"  {n} class{'es' if n != 1 else ''} attended")
        lines.append(f"  Subjects: {', '.join(sorted(subjects))}")
    else:
        lines.append("  No classes this week.")
    lines.append("")

    # --- NEXT WEEK PREVIEW (Mon-Fri) ---
    next_monday = monday + timedelta(days=7)
    lines.append("Next week:")
    has_next = False
    for offset in range(5):
        d = next_monday + timedelta(days=offset)
        day_events = [e for e in events_for_date(all_events, d)
                      if id(e) not in deadline_set]
        if day_events:
            has_next = True
            count = len(day_events)
            names = ", ".join(e.summary for e in sorted(day_events, key=lambda ev: ev.start))
            label = f"{count} class{'es' if count != 1 else ''}"
            lines.append(f"  {d:%a} {d.day} {d:%b}: {label} \u2014 {names}")
        else:
            lines.append(f"  {d:%a} {d.day} {d:%b}: free day")
    if not has_next:
        # replace the per-day lines if truly nothing
        pass  # the "free day" lines already cover it
    lines.append("")

    # --- upcoming opportunity deadlines ---
    dl_lines = _deadline_section(deadline_events, today)
    if dl_lines:
        lines.extend(dl_lines)
        lines.append("")

    # --- scholarship deadlines ---
    if scholarship_lines:
        lines.extend(scholarship_lines)
        lines.append("")

    lines.append("Have a great weekend!")
    return "\n".join(lines)
