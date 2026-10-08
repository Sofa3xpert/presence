"""Tests for the Student Daily Brief agent."""

from datetime import date
from pathlib import Path

from presence.agents.student_brief import compose_student_brief

SAMPLE_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:20261001T091500
DTEND:20261001T110000
SUMMARY:Linear Algebra Lecture
LOCATION:Room 305
END:VEVENT
BEGIN:VEVENT
DTSTART:20261001T130000
DTEND:20261001T143000
SUMMARY:Python Lab
LOCATION:Room 112
END:VEVENT
BEGIN:VEVENT
DTSTART:20261002T100000
DTEND:20261002T120000
SUMMARY:Calculus II
LOCATION:Room 201
END:VEVENT
BEGIN:VEVENT
DTSTART:20261005T140000
DTEND:20261005T160000
SUMMARY:Physics Seminar
LOCATION:Room 401
END:VEVENT
END:VCALENDAR
"""


def _setup_calendar(tmp_path: Path) -> Path:
    """Write a sample .ics into a temp calendars dir."""
    cal_dir = tmp_path / "calendars"
    cal_dir.mkdir()
    (cal_dir / "test.ics").write_text(SAMPLE_ICS)
    return cal_dir


def test_brief_contains_header(tmp_path):
    cal_dir = _setup_calendar(tmp_path)
    brief = compose_student_brief(cal_dir, today=date(2026, 10, 1))
    assert "Student Presence" in brief
    assert "Thu 1 Oct" in brief


def test_brief_shows_today_classes(tmp_path):
    cal_dir = _setup_calendar(tmp_path)
    brief = compose_student_brief(cal_dir, today=date(2026, 10, 1))
    assert "Linear Algebra Lecture" in brief
    assert "Python Lab" in brief
    assert "Room 305" in brief
    assert "2 class" in brief


def test_brief_shows_tomorrow(tmp_path):
    cal_dir = _setup_calendar(tmp_path)
    brief = compose_student_brief(cal_dir, today=date(2026, 10, 1))
    assert "Tomorrow" in brief
    assert "Calculus II" in brief


def test_brief_shows_week_ahead(tmp_path):
    cal_dir = _setup_calendar(tmp_path)
    brief = compose_student_brief(cal_dir, today=date(2026, 10, 1))
    assert "This week" in brief
    assert "Mon 5 Oct" in brief
    assert "Physics Seminar" not in brief  # week section shows counts, not names


def test_brief_free_day(tmp_path):
    cal_dir = _setup_calendar(tmp_path)
    brief = compose_student_brief(cal_dir, today=date(2026, 10, 3))
    assert "free day" in brief


def test_brief_no_calendar_dir(tmp_path):
    brief = compose_student_brief(tmp_path / "nonexistent", today=date(2026, 10, 1))
    assert "No calendar found" in brief


def test_brief_empty_calendar(tmp_path):
    cal_dir = tmp_path / "calendars"
    cal_dir.mkdir()
    (cal_dir / "empty.ics").write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    brief = compose_student_brief(cal_dir, today=date(2026, 10, 1))
    assert "empty" in brief.lower()


def test_brief_first_class_time(tmp_path):
    cal_dir = _setup_calendar(tmp_path)
    brief = compose_student_brief(cal_dir, today=date(2026, 10, 1))
    assert "First class at 09:15" in brief


def test_brief_ends_with_greeting(tmp_path):
    cal_dir = _setup_calendar(tmp_path)
    brief = compose_student_brief(cal_dir, today=date(2026, 10, 1))
    assert brief.strip().endswith("Have a good day!")


SAMPLE_ICS_WITH_DEADLINES = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:20261001T091500
DTEND:20261001T110000
SUMMARY:Linear Algebra Lecture
LOCATION:Room 305
END:VEVENT
BEGIN:VEVENT
DTSTART:20261001T235900
DTEND:20261001T235900
SUMMARY:Problem Set 5
END:VEVENT
BEGIN:VEVENT
DTSTART:20261002T170000
DTEND:20261002T170000
SUMMARY:Lab Report 2
END:VEVENT
BEGIN:VEVENT
DTSTART:20261005
SUMMARY:Essay Draft
END:VEVENT
END:VCALENDAR
"""


def _setup_with_deadlines(tmp_path: Path) -> Path:
    cal_dir = tmp_path / "calendars"
    cal_dir.mkdir()
    (cal_dir / "test.ics").write_text(SAMPLE_ICS_WITH_DEADLINES)
    return cal_dir


def test_brief_deadline_section(tmp_path):
    cal_dir = _setup_with_deadlines(tmp_path)
    brief = compose_student_brief(cal_dir, today=date(2026, 10, 1))
    assert "Upcoming deadlines" in brief
    assert "Problem Set 5" in brief
    assert "TODAY" in brief


def test_brief_deadlines_not_in_classes(tmp_path):
    cal_dir = _setup_with_deadlines(tmp_path)
    brief = compose_student_brief(cal_dir, today=date(2026, 10, 1))
    # "Today (1 class)" — the deadline should NOT count as a class
    assert "1 class)" in brief
    # The lecture should still be there
    assert "Linear Algebra Lecture" in brief


def test_brief_deadline_urgency_labels(tmp_path):
    cal_dir = _setup_with_deadlines(tmp_path)
    brief = compose_student_brief(cal_dir, today=date(2026, 10, 1))
    assert "TODAY" in brief            # Problem Set 5 — same day
    assert "tomorrow" in brief         # Lab Report 2 — next day
    assert "in 4 days" in brief        # Essay Draft — Oct 5
