"""Google Sheet mirror: push, pull of a person's edits, hand-added rows, refusals."""

import json
import os
from datetime import date

import pytest
from fake_sheet import FakeClient

from presence.adapters import google_client as gc
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
    with pytest.raises(gsheet.SheetError, match="isn't included in this build"):
        gsheet.start_oauth(tmp_path)


# ------------------------------------------------------------ sign-in flow


class _FakeCreds:
    def to_json(self):
        return json.dumps({"token": "t", "refresh_token": "r", "scopes": [gsheet.SCOPE_FILES]})


class _FakeFlow:
    calls: list[dict] = []

    def __init__(self, cfg, scopes):
        self.cfg, self.scopes = cfg, scopes

    @classmethod
    def from_client_config(cls, cfg, scopes):
        return cls(cfg, scopes)

    def run_local_server(self, **kw):
        _FakeFlow.calls.append({"cfg": self.cfg, "scopes": self.scopes, **kw})
        return _FakeCreds()


class _FailingFlow(_FakeFlow):
    def run_local_server(self, **kw):
        raise TimeoutError("nobody signed in")


def _run_oauth(tmp_path, monkeypatch, flow=_FakeFlow):
    _FakeFlow.calls.clear()
    monkeypatch.setattr(gsheet, "_flow_class", lambda: flow)
    st = gsheet.start_oauth(tmp_path)
    gsheet._oauth_thread.join(5)
    return st


@pytest.fixture
def no_builtin(monkeypatch):
    monkeypatch.delenv("PRESENCE_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("PRESENCE_GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(gc, "CLIENT_ID", "__PRESENCE_GOOGLE_CLIENT_ID__")
    monkeypatch.setattr(gc, "CLIENT_SECRET", "__PRESENCE_GOOGLE_CLIENT_SECRET__")


@pytest.fixture
def builtin(monkeypatch):
    monkeypatch.setenv("PRESENCE_GOOGLE_CLIENT_ID", "1-test.apps.googleusercontent.com")
    monkeypatch.setenv("PRESENCE_GOOGLE_CLIENT_SECRET", "test-secret-not-real")


def test_builtin_client_signs_in_with_drive_file_only(tmp_path, monkeypatch, builtin):
    assert gsheet.connection(tmp_path)["builtin"] is True
    (tmp_path / gsheet.RECONNECT_FLAG).write_text("")
    _run_oauth(tmp_path, monkeypatch)
    call = _FakeFlow.calls[0]
    assert call["cfg"]["installed"]["client_id"] == "1-test.apps.googleusercontent.com"
    assert call["cfg"]["installed"]["client_secret"] == "test-secret-not-real"
    assert call["scopes"] == [gsheet.SCOPE_FILES]
    assert call["host"] == "127.0.0.1" and call["port"] == 0 and call["open_browser"] is True
    assert call["timeout_seconds"] == 300 and "close this tab" in call["success_message"]
    assert oct(os.stat(tmp_path / gsheet.OAUTH_TOKEN).st_mode)[-3:] == "600"
    assert not (tmp_path / gsheet.RECONNECT_FLAG).exists()  # a fresh sign-in clears it
    st = gsheet.oauth_status(tmp_path)
    assert st["connected"] and st["done"] and not st["running"] and st["error"] is None
    assert gsheet.connection(tmp_path) == {
        "kind": "oauth", "email": "", "builtin": True, "has_client": True,
        "needs_reconnect": False,
    }


def test_uploaded_client_is_used_only_without_a_builtin(tmp_path, monkeypatch, no_builtin):
    assert gsheet.connection(tmp_path)["builtin"] is False
    own = {"installed": {"client_id": "own-id", "client_secret": "own-s",
                         "auth_uri": "https://a", "token_uri": "https://t"}}
    gsheet.save_oauth_client(tmp_path, json.dumps(own).encode())
    _run_oauth(tmp_path, monkeypatch)
    assert _FakeFlow.calls[0]["cfg"] == own
    monkeypatch.setenv("PRESENCE_GOOGLE_CLIENT_ID", "1-test.apps.googleusercontent.com")
    _run_oauth(tmp_path, monkeypatch)
    assert _FakeFlow.calls[0]["cfg"]["installed"]["client_id"].startswith("1-test")


def test_sign_in_failure_is_reported_not_raised(tmp_path, monkeypatch, builtin):
    st = _run_oauth(tmp_path, monkeypatch, flow=_FailingFlow)
    assert st["error"].startswith("TimeoutError") and not st["running"]
    assert not gsheet.oauth_status(tmp_path)["connected"]


# ------------------------------------------------------------ quota-aware client


class _Resp:
    def __init__(self, status, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Creds:
    valid = True
    token = "tok"

    def refresh(self, request):
        raise AssertionError("valid creds are not refreshed")


class _ExpiredCreds:
    valid = False
    token = ""

    def refresh(self, request):
        from google.auth.exceptions import RefreshError

        raise RefreshError("invalid_grant: Token has been expired or revoked.")


def _http(monkeypatch, responses):
    calls, naps = [], []
    it = iter(responses)

    def fake_request(method, url, **kw):
        calls.append((method, url))
        return next(it)

    monkeypatch.setattr(gsheet.requests, "request", fake_request)
    monkeypatch.setattr(gsheet, "_sleep", naps.append)
    return calls, naps


def test_backoff_on_429_then_success(monkeypatch):
    calls, naps = _http(monkeypatch, [
        _Resp(429, {"error": {"message": "slow down"}}),
        _Resp(403, {"error": {"message": "Quota", "errors": [{"reason": "rateLimitExceeded"}]}}),
        _Resp(200, {"values": [["ID"]]}),
    ])
    assert gsheet.SheetClient(_Creds()).read("sid", "'Tracker'!A1:L") == [["ID"]]
    assert len(calls) == 3 and len(naps) == 2
    assert 1 <= naps[0] < 2 and 2 <= naps[1] < 3  # 2**n plus up to a second of jitter


def test_gives_up_after_max_tries(monkeypatch):
    calls, naps = _http(monkeypatch, [_Resp(429, {"error": {"message": "x"}})] * 10)
    with pytest.raises(gsheet.SheetError, match="busy"):
        gsheet.SheetClient(_Creds()).read("sid", "A1")
    assert len(calls) == gsheet.MAX_TRIES and len(naps) == gsheet.MAX_TRIES - 1
    assert max(naps) < 17


def test_plain_403_is_not_retried(monkeypatch):
    calls, naps = _http(monkeypatch, [_Resp(403, {"error": {"message": "forbidden"}})])
    with pytest.raises(gsheet.SheetError, match="403: forbidden"):
        gsheet.SheetClient(_Creds()).read("sid", "A1")
    assert len(calls) == 1 and naps == []


def test_invalid_grant_asks_to_reconnect(tmp_path, monkeypatch):
    calls, _ = _http(monkeypatch, [])
    (tmp_path / gsheet.OAUTH_TOKEN).write_text("{}")
    with pytest.raises(gsheet.SheetError, match="Google needs you to reconnect"):
        gsheet.SheetClient(_ExpiredCreds(), tmp_path).read("sid", "A1")
    assert calls == [] and gsheet.connection(tmp_path)["needs_reconnect"] is True
    gsheet.disconnect(tmp_path)
    assert not (tmp_path / gsheet.RECONNECT_FLAG).exists()


def test_401_asks_to_reconnect_too(tmp_path, monkeypatch):
    _http(monkeypatch, [_Resp(401, {"error": {"message": "Invalid Credentials"}})])
    (tmp_path / gsheet.OAUTH_TOKEN).write_text("{}")
    with pytest.raises(gsheet.SheetError, match="reconnect"):
        gsheet.SheetClient(_Creds(), tmp_path).read("sid", "A1")
    assert gsheet.connection(tmp_path)["needs_reconnect"] is True


def test_sync_is_three_calls_regardless_of_rows(tmp_path):
    t = _tracker(tmp_path)
    for i in range(40):
        t.ingest(Candidate(company=f"C{i}", title="Role", url=f"https://x/{i}", source="lever"))

    class Counting(FakeClient):
        def __init__(self):
            super().__init__()
            self.calls = []

        def read(self, *a):
            self.calls.append("read")
            return super().read(*a)

        def clear(self, *a):
            self.calls.append("clear")
            return super().clear(*a)

        def write(self, *a):
            self.calls.append("write")
            return super().write(*a)

    c = Counting()
    r = gsheet.sync(t, c, "sid", "Tracker", tmp_path / "s.json", today=date(2026, 9, 14))
    assert r.pushed == len(t.list()) >= 40 and c.calls == ["read", "clear", "write"]
