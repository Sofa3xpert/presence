from datetime import date

import pytest

from presence.tracker import Candidate, ConventionError, Tracker


@pytest.fixture
def tracker(tmp_path):
    t = Tracker(tmp_path / "tracker.db")
    yield t
    t.close()


LI = Candidate(company="Palantir", title="Software Engineer, New Grad",
               url="https://www.linkedin.com/jobs/view/111?ref=a", source="linkedin", tier="Reach")


def test_migrate_is_idempotent(tmp_path):
    t = Tracker(tmp_path / "t.db")
    assert t.migrate() == []  # already applied by __init__
    t.close()


def test_ingest_and_dedupe(tracker):
    job, created = tracker.ingest(LI)
    assert created and job.status == "to_apply" and job.tier == "Reach"
    # same link with different tracking params -> duplicate
    again, created = tracker.ingest(LI.model_copy(update={"url": LI.url + "&x=1"}))
    assert not created and again.id == job.id
    # same company + identical normalised title, hand-added without a link -> duplicate
    manual = Candidate(company="Palantir Technologies",
                       title="Graduate Software Engineer, New Grad")
    dup, created = tracker.ingest(manual)
    assert not created and dup.id == job.id
    # same title but a DIFFERENT link -> a different posting; only a similarity hint
    repost = Candidate(company="Palantir", title="Software Engineer, New Grad",
                       url="https://jobs.lever.co/palantir/abc", source="board")
    assert [j.id for j in tracker.find_similar(repost)] == [job.id]
    new, created = tracker.ingest(repost)
    assert created and new.id != job.id
    # different role at the same company -> new row, no similarity hint
    other = Candidate(company="Palantir", title="Forward Deployed Software Engineer",
                      url="https://jobs.lever.co/palantir/def")
    assert tracker.find_similar(other) == []
    new2, created = tracker.ingest(other)
    assert created and new2.id not in (job.id, new.id)
    assert len(tracker.list()) == 3


def test_events_drive_status_and_timeline(tracker):
    job, _ = tracker.ingest(LI)
    tracker.record_event(job.id, "applied", date(2026, 8, 29))
    tracker.record_event(job.id, "assessment", date(2026, 9, 1), "CCAT")
    tracker.record_event(job.id, "note", date(2026, 9, 2), "target 35+")
    assert tracker.get(job.id).status == "assessment"
    tracker.record_event(job.id, "rejected", date(2026, 9, 8))
    assert tracker.get(job.id).status == "rejected"
    assert tracker.timeline(job.id) == "29 Aug → CCAT 1 Sep → rejected 8 Sep"
    with pytest.raises(ConventionError, match="closed"):
        tracker.record_event(job.id, "applied", date(2026, 9, 9))
    tracker.record_event(job.id, "note", date(2026, 9, 9), "notes still allowed")
    with pytest.raises(ConventionError, match="unknown event"):
        tracker.record_event(job.id, "ghosted", date(2026, 9, 9))


def test_cooldown_ingests_as_ignored(tracker):
    tracker.add_cooldown("Bending Spoons", date(2027, 8, 24), "1-year lockout")
    job, created = tracker.ingest(
        Candidate(company="Bending Spoons", title="Data Scientist", url="https://x/1"),
        today=date(2026, 9, 12))
    assert created and job.status == "ignored"
    assert "cooldown until 2027-08-24" in tracker.timeline(job.id) or \
        tracker.events(job.id)[0][2].startswith("cooldown")
    assert tracker.cooling_until("Bending Spoons", date(2027, 9, 1)) is None


def test_extra_is_stored_untouched(tracker):
    job, _ = tracker.ingest(LI)
    tracker.set_extra(job.id, pinned=True, salary_floor=50000)
    tracker.set_extra(job.id, contact="Ada")
    assert tracker.get(job.id).extra == {"pinned": True, "salary_floor": 50000, "contact": "Ada"}


def test_export_and_backup(tracker, tmp_path):
    job, _ = tracker.ingest(LI)
    tracker.record_event(job.id, "applied", date(2026, 8, 31))
    n = tracker.export_csv(tmp_path / "out.csv")
    text = (tmp_path / "out.csv").read_text()
    assert n == 1 and "31 Aug" in text and "Palantir" in text
    dest = tracker.backup(tmp_path / "bak" / "tracker.db")
    copy = Tracker(dest)
    assert copy.get(job.id).company == "Palantir"
    copy.close()
