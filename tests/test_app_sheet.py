"""The Google Sheet step and imports through the app."""

import io
import json

from fake_sheet import FakeClient

from presence.adapters import gsheet
from presence.app import server
from presence.app.config_io import read_yaml
from presence.tracker import Candidate, Tracker


def _client(tmp_path):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def test_backend_choice_keeps_other_keys_and_csv_import(tmp_path):
    c = _client(tmp_path)
    c.post("/setup/tracker", data={"backend": "sheet"})
    assert read_yaml(tmp_path / "presence.yaml")["tracker"]["backend"] == "sheet"
    page = c.get("/").data.decode()
    assert "Connect Google" in page and "Create sheet" not in page  # not connected yet
    csv = ("Company,Role,Link,Status,Applied\n"
           "Acme,AI Engineer,https://x/1,Applied,3 Sep\nBeta,Analyst,,,\n")
    r = c.post(
        "/tracker/import",
        data={"csv": (io.BytesIO(csv.encode()), "t.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"imported: 2 added" in r.data
    t = Tracker(tmp_path / "tracker.db")
    assert (
        t.get(1).status == "applied"
        and t.timeline(1) == "3 Sep"
        and t.get(1).source == "csv-import"
    )
    t.close()


def test_service_account_create_share_sync(tmp_path, monkeypatch):
    c = _client(tmp_path)
    fake = FakeClient()
    monkeypatch.setattr(server, "sheet_client_for", lambda d: fake)
    c.post("/setup/tracker", data={"backend": "sheet"})
    key = json.dumps({"type": "service_account", "client_email": "p@x.iam.gserviceaccount.com"})
    r = c.post(
        "/google/service-account",
        data={"key": (io.BytesIO(key.encode()), "k.json")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"service account saved: p@x" in r.data
    r = c.post(
        "/sheet/create",
        data={"title": "My tracker", "share_with": "me@gmail.com"},
        follow_redirects=True,
    )
    assert b"sheet created" in r.data and fake.shared == [("sid1", "me@gmail.com")]
    tr = read_yaml(tmp_path / "presence.yaml")["tracker"]
    assert tr["sheet_id"] == "sid1" and tr["sheet_url"].endswith("/sid1") and tr["tab"] == "Tracker"
    t = Tracker(tmp_path / "tracker.db")
    t.ingest(Candidate(company="Acme", title="AI Engineer", url="https://x/1", source="greenhouse"))
    t.close()
    r = c.post("/sheet/sync", follow_redirects=True)
    assert b"1 row(s) pushed" in r.data and fake.rows[0] == gsheet.HEADER
    assert b"last sync" in c.get("/").data
    r = c.post("/google/disconnect", follow_redirects=True)
    assert b"Google disconnected" in r.data and gsheet.connection(tmp_path)["kind"] == ""


def test_connect_refuses_foreign_sheet_but_imports_it(tmp_path, monkeypatch):
    c = _client(tmp_path)
    fake = FakeClient(
        rows=[
            ["Company", "Role", "Status", "Link"],
            ["Acme", "AI Engineer", "Interview", "https://x/1"],
        ]
    )
    monkeypatch.setattr(server, "sheet_client_for", lambda d: fake)
    c.post("/setup/tracker", data={"backend": "sheet"})
    r = c.post(
        "/sheet/connect",
        data={"sheet": "https://docs.google.com/spreadsheets/d/old1/edit"},
        follow_redirects=True,
    )
    assert b"different columns" in r.data
    r = c.post("/sheet/import", data={"sheet": "old1"}, follow_redirects=True)
    assert b"imported: 1 added" in r.data
    t = Tracker(tmp_path / "tracker.db")
    assert t.get(1).status == "interview" and t.get(1).source == "sheet-import"
    t.close()


def test_sync_without_google_explains(tmp_path):
    c = _client(tmp_path)
    r = c.post("/sheet/sync", follow_redirects=True)
    assert b"create or connect a sheet first" in r.data
