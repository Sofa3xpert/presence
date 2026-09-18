"""Tests for the iCal connector."""

from datetime import date, datetime

from presence.connectors.ical import (
    CalendarEvent,
    events_for_date,
    parse_ics,
    upcoming_events,
)

SAMPLE_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//University//Timetable//EN
BEGIN:VEVENT
DTSTART:20261001T091500
DTEND:20261001T110000
SUMMARY:Linear Algebra Lecture
LOCATION:Room 305
DESCRIPTION:Chapter 3 — Vector spaces
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
SUMMARY:Calculus Exam
LOCATION:Hall A
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=Europe/Moscow:20261003T140000
DTEND;TZID=Europe/Moscow:20261003T160000
SUMMARY:Physics Seminar
LOCATION:Room 201
END:VEVENT
BEGIN:VEVENT
DTSTART:20261005
SUMMARY:Essay Deadline
DESCRIPTION:Submit via LMS by midnight
END:VEVENT
END:VCALENDAR
"""


def test_parse_ics_basic():
    events = parse_ics(SAMPLE_ICS)
    assert len(events) == 5
    assert events[0].summary == "Linear Algebra Lecture"
    assert events[0].location == "Room 305"
    assert events[0].start == datetime(2026, 10, 1, 9, 15)
    assert events[0].end == datetime(2026, 10, 1, 11, 0)


def test_parse_ics_timezone():
    events = parse_ics(SAMPLE_ICS)
    physics = [e for e in events if "Physics" in e.summary][0]
    assert physics.start == datetime(2026, 10, 3, 14, 0)


def test_parse_ics_allday():
    events = parse_ics(SAMPLE_ICS)
    deadline = [e for e in events if "Deadline" in e.summary][0]
    assert deadline.start == datetime(2026, 10, 5, 0, 0)
    assert deadline.end is None


def test_event_date():
    events = parse_ics(SAMPLE_ICS)
    assert events[0].date == date(2026, 10, 1)


def test_duration_minutes():
    events = parse_ics(SAMPLE_ICS)
    assert events[0].duration_minutes == 105  # 09:15 to 11:00


def test_format_time():
    events = parse_ics(SAMPLE_ICS)
    assert events[0].format_time() == "09:15–11:00"


def test_format_time_no_end():
    events = parse_ics(SAMPLE_ICS)
    deadline = [e for e in events if "Deadline" in e.summary][0]
    assert deadline.format_time() == "00:00"


def test_str():
    events = parse_ics(SAMPLE_ICS)
    assert str(events[0]) == "09:15–11:00 Linear Algebra Lecture — Room 305"


def test_events_for_date():
    events = parse_ics(SAMPLE_ICS)
    oct1 = events_for_date(events, date(2026, 10, 1))
    assert len(oct1) == 2
    assert oct1[0].summary == "Linear Algebra Lecture"
    assert oct1[1].summary == "Python Lab"


def test_upcoming_events():
    events = parse_ics(SAMPLE_ICS)
    upcoming = upcoming_events(events, days=3, today=date(2026, 10, 1))
    assert len(upcoming) == 4  # Oct 1 (2), Oct 2 (1), Oct 3 (1)


def test_source_file():
    events = parse_ics(SAMPLE_ICS, source_file="timetable.ics")
    assert all(e.source_file == "timetable.ics" for e in events)


def test_unescape():
    ics = """\
BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20261001T090000
SUMMARY:Intro to AI\\, Machine Learning
LOCATION:Room 100
END:VEVENT
END:VCALENDAR
"""
    events = parse_ics(ics)
    assert events[0].summary == "Intro to AI, Machine Learning"


def test_empty_ics():
    assert parse_ics("") == []
    assert parse_ics("BEGIN:VCALENDAR\nEND:VCALENDAR") == []
