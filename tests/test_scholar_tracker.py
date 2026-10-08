"""Tests for the Scholarship Tracker."""

from datetime import date

from presence.agents.scholar_tracker import (
    ScholarshipTracker,
    scholarship_brief_section,
)


def test_add_and_list(tmp_path):
    tracker = ScholarshipTracker(tmp_path / "s.json")
    assert tracker.all() == []
    s = tracker.add(name="DS&AI Hall Scholarship", deadline="2026-11-01")
    assert len(tracker.all()) == 1
    assert s.name == "DS&AI Hall Scholarship"
    assert s.status == "to_apply"


def test_persistence(tmp_path):
    path = tmp_path / "s.json"
    t1 = ScholarshipTracker(path)
    t1.add(name="Test", deadline="2026-12-01")
    t2 = ScholarshipTracker(path)
    assert len(t2.all()) == 1
    assert t2.all()[0].name == "Test"


def test_update_status(tmp_path):
    tracker = ScholarshipTracker(tmp_path / "s.json")
    s = tracker.add(name="Applied One")
    tracker.update(s.id, status="applied")
    assert tracker.get(s.id).status == "applied"


def test_remove(tmp_path):
    tracker = ScholarshipTracker(tmp_path / "s.json")
    s = tracker.add(name="Remove Me")
    assert tracker.remove(s.id)
    assert tracker.all() == []
    assert not tracker.remove("nonexistent")


def test_open_filter(tmp_path):
    tracker = ScholarshipTracker(tmp_path / "s.json")
    tracker.add(name="A", status="to_apply")
    tracker.add(name="B", status="applied")
    tracker.add(name="C", status="won")
    tracker.add(name="D", status="rejected")
    assert len(tracker.open()) == 2


def test_upcoming_deadlines(tmp_path):
    tracker = ScholarshipTracker(tmp_path / "s.json")
    tracker.add(name="Soon", deadline="2026-10-05", status="to_apply")
    tracker.add(name="Far", deadline="2027-01-01", status="to_apply")
    tracker.add(name="Already applied", deadline="2026-10-03", status="applied")
    tracker.add(name="No deadline", status="to_apply")
    upcoming = tracker.upcoming_deadlines(days=14, today=date(2026, 9, 29))
    assert len(upcoming) == 1
    assert upcoming[0].name == "Soon"


def test_expired_check(tmp_path):
    tracker = ScholarshipTracker(tmp_path / "s.json")
    tracker.add(name="Past", deadline="2026-09-01", status="to_apply")
    tracker.add(name="Future", deadline="2026-12-01", status="to_apply")
    count = tracker.expired_check(today=date(2026, 9, 29))
    assert count == 1
    assert tracker.get(tracker.all()[0].id).status == "expired"
    assert tracker.get(tracker.all()[1].id).status == "to_apply"


def test_brief_section(tmp_path):
    tracker = ScholarshipTracker(tmp_path / "s.json")
    tracker.add(name="Zou Xin AI Scholarship", deadline="2026-10-10", status="to_apply")
    tracker.add(name="Hall Scholarship", deadline="2026-10-01", status="to_apply")
    lines = scholarship_brief_section(tracker, today=date(2026, 9, 29))
    assert len(lines) == 3  # header + 2 scholarships
    assert "Opportunity deadlines (2):" in lines[0]
    assert "Hall Scholarship" in lines[1]  # sorted by deadline — Oct 1 first
    assert "in 2 days" in lines[1]
    assert "Zou Xin AI Scholarship" in lines[2]
    assert "in 11 days" in lines[2]


def test_brief_section_empty(tmp_path):
    tracker = ScholarshipTracker(tmp_path / "s.json")
    lines = scholarship_brief_section(tracker, today=date(2026, 9, 29))
    assert lines == []
