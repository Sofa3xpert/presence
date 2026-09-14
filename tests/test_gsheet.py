"""Google Sheet mirror: push, pull of a person's edits, hand-added rows, refusals."""

import json
import os
from datetime import date

import pytest
from fake_sheet import FakeClient

from presence.adapters import gsheet
from presence.tracker import Candidate, Tracker
from presence.tracker.conventions import parse_status


def _tracker(tmp_path):
    t = Tracker(tmp_path / "t.db")
    t.ingest(Candidate(company="Acme", title="AI Engineer", url="https://x/1", source="greenhouse"))
    t.ingest(Candidate(company="Beta", title="Data Analyst", url="https://x/2", source="lever"))
    return t


def _row(client, job_id):
    return next(r for r in client.rows[1:] if r[0] == str(job_id))


def test_first_sync_pushes_everything(tmp_path):
    t, c, state = _tracker(tmp_path), FakeClient(), tmp_path / "s.json"
    r = gsheet.sync(t, c, "sid", "Tracker", state, today=date(2026, 9, 14))
    assert r.pushed == 2 and r.pulled == [] and c.rows[0] == gsheet.HEADER
    assert c.rows[1][:3] == ["2", "Beta", "Data Analyst"]  # newest first
    assert json.loads(state.read_text())["snapshot"]["1"]["Status"] == "to apply"
    assert oct(os.stat(state).st_mode)[-3:] == "600"


def test_person_edits_come_back_as_events(tmp_path):
    t, c, state = _tracker(tmp_path), FakeClient(), tmp_path / "s.json"
    gsheet.sync(t, c, "sid", "Tracker", state, today=date(2026, 9, 14))
    row = _row(c, 1)
    row[4], row[6], row[7] = "Applied", "follow up Friday", "referral via Sam"
    r = gsheet.sync(t, c, "sid", "Tracker", state, today=date(2026, 9, 15))
    assert len(r.pulled) == 3 and t.get(1).status == "applied"
    assert t.timeline(1) == "15 Sep" and t.get(1).extra["next_action"] == "follow up Friday"
    assert t.get(1).note == "referral via Sam"
    assert _row(c, 1)[4:6] == ["applied", "15 Sep"]  # pushed back, rendered
    r = gsheet.sync(t, c, "sid", "Tracker", state, today=date(2026, 9, 16))
    assert r.pulled == [] and r.issues == []  # nothing changed → nothing happens


def test_unknown_status_and_closed_job_become_issues(tmp_path):
    t, c, state = _tracker(tmp_path), FakeClient(), tmp_path / "s.json"
    t.record_event(2, "rejected", date(2026, 9, 1))
    gsheet.sync(t, c, "sid", "Tracker", state, today=date(2026, 9, 14))
    _row(c, 1)[4] = "ghosted"
    _row(c, 2)[4] = "applied"
    r = gsheet.sync(t, c, "sid", "Tracker", state, today=date(2026, 9, 15))
    assert len(r.issues) == 2 and r.pulled == []
    assert any("unknown status 'ghosted'" in i for i in r.issues)
    assert any("only notes" in i for i in r.issues) and t.get(2).status == "rejected"
    assert _row(c, 1)[4] == "to apply" and _row(c, 2)[4] == "rejected"  # sheet corrected


def test_hand_added_row_is_tracked(tmp_path):
    t, c, state = _tracker(tmp_path), FakeClient(), tmp_path / "s.json"
    gsheet.sync(t, c, "sid", "Tracker", state, today=date(2026, 9, 14))
    c.rows.append(
        ["", "Gamma", "ML Engineer", "London", "applied", "", "call back", "", "https://x/3"]
    )
    r = gsheet.sync(t, c, "sid", "Tracker", state, today=date(2026, 9, 15))
    assert r.added_from_sheet == 1
    j = next(j for j in t.list() if j.company == "Gamma")
    assert j.status == "applied" and j.source == "sheet" and j.extra["next_action"] == "call back"
    assert _row(c, j.id)[1] == "Gamma"  # it now carries an ID


def test_refuses_a_sheet_with_other_columns(tmp_path):
    t = _tracker(tmp_path)
    c = FakeClient(rows=[["Tier", "Today", "Company"], ["A", "", "Acme"]])
    with pytest.raises(gsheet.SheetError, match="different columns"):
        gsheet.sync(t, c, "sid", "Tracker", tmp_path / "s.json")
    assert gsheet.connect_existing(FakeClient(), "sid")[1] == "Tracker"  # empty sheet is fine
    with pytest.raises(gsheet.SheetError, match="no sheets"):
        gsheet.connect_existing(FakeClient(tabs=()), "sid")


def test_status_labels_and_sheet_ids():
    assert parse_status("Interview done") == "interview_done"
    assert parse_status("to-apply") == "to_apply" and parse_status("ghosted") is None
    assert parse_status("Ignored (required Masters)") == "ignored"
    url = "https://docs.google.com/spreadsheets/d/1AbC_d-e/edit#gid=0"
    assert gsheet.sheet_id_from(url) == "1AbC_d-e" and gsheet.sheet_id_from(" 1AbC ") == "1AbC"


def test_credential_files(tmp_path):
    with pytest.raises(gsheet.SheetError):
        gsheet.save_service_account(tmp_path, b"{}")
    key = {"type": "service_account", "client_email": "p@x.iam.gserviceaccount.com"}
    assert gsheet.save_service_account(tmp_path, json.dumps(key).encode()).startswith("p@")
    assert oct(os.stat(tmp_path / gsheet.SA_FILE).st_mode)[-3:] == "600"
    assert gsheet.connection(tmp_path)["kind"] == "service_account"
    gsheet.disconnect(tmp_path)
    assert gsheet.connection(tmp_path)["kind"] == ""
    with pytest.raises(gsheet.SheetError):
        gsheet.save_oauth_client(tmp_path, b'{"web": {}}')
    with pytest.raises(gsheet.SheetError, match="client file first"):
        gsheet.start_oauth(tmp_path)
