"""The Google Sheet step and imports through the app."""

import io
import json

import pytest
from fake_sheet import FakeClient

from presence.adapters import gsheet
from presence.app import server
from presence.app.config_io import read_yaml
from presence.tracker import Candidate, Tracker


def _client(tmp_path):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def source_build(monkeypatch):
    """No Google client in this build (what a source checkout looks like)."""
    monkeypatch.delenv("PRESENCE_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("PRESENCE_GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(gsheet, "has_builtin_client", lambda: False)
    monkeypatch.setattr(gsheet, "builtin_client_config", lambda: None)


@pytest.fixture
def release_build(monkeypatch):
    monkeypatch.setenv("PRESENCE_GOOGLE_CLIENT_ID", "1-test.apps.googleusercontent.com")
    monkeypatch.setenv("PRESENCE_GOOGLE_CLIENT_SECRET", "test-secret-not-real")


def test_backend_choice_keeps_other_keys_and_csv_import(tmp_path, source_build):
    c = _client(tmp_path)
    page = c.get("/").data.decode()
    assert "Kept on this computer" in page and "Connect Google" not in page  # SQLite default
    c.post("/setup/tracker", data={"backend": "sheet"})
    assert read_yaml(tmp_path / "presence.yaml")["tracker"]["backend"] == "sheet"
    page = c.get("/").data.decode()
    assert "Google sign-in isn't included in this build" in page
    assert "<summary>Advanced</summary>" in page and "Add client" in page and "Add key" in page
    assert "Connect Google" not in page and "Create sheet" not in page  # nothing to sign in with
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


def test_service_account_create_share_sync(tmp_path, monkeypatch, source_build):
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
    page = c.get("/").data.decode()
    assert "not synced yet" in page and "Sync now" in page
    assert "Sheet to import (shared with the service account)" in page
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


def test_release_build_shows_one_button_then_makes_the_sheet(tmp_path, monkeypatch, release_build):
    c = _client(tmp_path)
    c.post("/setup/tracker", data={"backend": "sheet"})
    page = c.get("/").data.decode()
    assert "Connect Google" in page and "only see the sheet it creates for you" in page
    assert "Advanced" not in page and "Add client" not in page and "Add key" not in page
    assert "Connect an empty sheet" not in page and "signing in" not in page

    started = {}
    monkeypatch.setattr(gsheet, "start_oauth", lambda data: started.update(running=True))
    monkeypatch.setattr(gsheet, "oauth_status", lambda data: {
        "running": bool(started) and not (data / gsheet.OAUTH_TOKEN).exists(),
        "done": False, "error": None, "connected": (data / gsheet.OAUTH_TOKEN).exists(),
    })
    r = c.post("/google/oauth/start", follow_redirects=True)
    assert b"choose your account there" in r.data and started == {"running": True}
    page = r.data.decode()
    assert "signing in…" in page and "google/oauth/status" in page and "google/oauth/finish" in page
    st = c.get("/google/oauth/status").get_json()
    assert st["running"] and not st["connected"] and st["sheet_id"] == ""

    # the browser flow finished: a token landed; the page asks for the sheet once
    (tmp_path / gsheet.OAUTH_TOKEN).write_text("{}")
    fake = FakeClient()
    monkeypatch.setattr(server, "sheet_client_for", lambda d: fake)
    t = Tracker(tmp_path / "tracker.db")
    t.ingest(Candidate(company="Acme", title="AI Engineer", url="https://x/1", source="greenhouse"))
    t.close()
    r = c.post("/google/oauth/finish")
    assert r.get_json() == {"created": True, "sheet_url": "https://docs.google.com/spreadsheets/d/sid1"}
    assert fake.rows[0] == gsheet.HEADER and fake.rows[1][1] == "Acme"  # filled right away
    tr = read_yaml(tmp_path / "presence.yaml")["tracker"]
    assert tr["sheet_id"] == "sid1" and tr["backend"] == "sheet"
    r = c.post("/google/oauth/finish")  # a second poll must not make a second sheet
    assert r.get_json()["created"] is False and fake.writes == 1
    page = c.get("/").data.decode()
    assert "signed in with Google" in page and "Open your sheet" in page and "Sync now" in page
    assert "your tracker sheet is ready" in page and "last sync" in page
    assert "share_with" not in page and "Sheet to import" not in page  # drive.file: own sheet only
    assert "Comma Separated Values" in page and 'name="csv"' in page  # CSV import stays


def test_finish_needs_a_connection_and_reports_google_errors(tmp_path, monkeypatch, release_build):
    c = _client(tmp_path)
    c.post("/setup/tracker", data={"backend": "sheet"})
    assert c.post("/google/oauth/finish").status_code == 409

    class Broken(FakeClient):
        def create(self, title, tab="Tracker"):
            raise gsheet.SheetError("Google API 403: Drive API is not enabled")

    (tmp_path / gsheet.OAUTH_TOKEN).write_text("{}")
    monkeypatch.setattr(server, "sheet_client_for", lambda d: Broken())
    r = c.post("/google/oauth/finish")
    assert r.status_code == 502 and "Drive API" in r.get_json()["error"]
    assert not read_yaml(tmp_path / "presence.yaml").get("tracker", {}).get("sheet_id")
    page = c.get("/").data.decode()
    assert "Create my tracker sheet" in page  # the manual way stays available


def test_expired_google_token_shows_reconnect(tmp_path, monkeypatch, release_build):
    c = _client(tmp_path)
    c.post("/setup/tracker", data={"backend": "sheet", "sheet_id": "sid1"})
    server_tr = read_yaml(tmp_path / "presence.yaml")
    server_tr["tracker"].update(sheet_id="sid1", sheet_url="https://s/sid1", tab="Tracker")
    from presence.app.config_io import write_yaml
    write_yaml(tmp_path / "presence.yaml", server_tr)
    (tmp_path / gsheet.OAUTH_TOKEN).write_text("{}")

    class Expired(FakeClient):
        def read(self, sheet_id, rng):
            gsheet.mark_reconnect(tmp_path)
            raise gsheet.SheetError(gsheet.RECONNECT_MSG)

    monkeypatch.setattr(server, "sheet_client_for", lambda d: Expired())
    r = c.post("/sheet/sync", follow_redirects=True)
    page = r.data.decode()
    assert "Google needs you to reconnect" in page and "Reconnect Google" in page
    assert "Disconnect" not in page
    monkeypatch.setattr(gsheet, "start_oauth", lambda data: None)
    r = c.post("/google/oauth/start", follow_redirects=True)
    assert b"choose your account there" in r.data


def test_source_build_own_client_file_path_still_works(tmp_path, monkeypatch, source_build):
    c = _client(tmp_path)
    c.post("/setup/tracker", data={"backend": "sheet"})
    own = json.dumps({"installed": {"client_id": "own", "auth_uri": "a", "token_uri": "t"}})
    r = c.post(
        "/google/oauth/client",
        data={"client": (io.BytesIO(own.encode()), "c.json")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"OAuth client saved" in r.data
    page = r.data.decode()
    assert "Connect Google" in page and "client file ready" in page
    assert "Google sign-in isn't included in this build" in page  # still a source build
