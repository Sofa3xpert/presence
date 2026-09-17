"""Google Sheet mirror of the tracker.

SQLite stays the source of truth; the sheet is where a person edits. Presence
owns the columns it fills (identity, timeline, link, source); the person owns
Status, Next action and Notes. A sync pulls those edits first, then pushes
every row back, so the sheet always ends up looking like the tracker. A row a
person adds by hand (no ID) becomes a tracked job.

Two ways in: the person's own Google account via OAuth on the loopback address,
limited to files Presence created — the narrow drive.file scope — using the
client a release build ships with (or one the person uploads on a source
build); or a service account (developer path: works with any sheet shared
with it).

Every Google project shares one quota pool across all installs, so a sync is a
fixed three calls (one read, one clear, one write) and the client backs off
with jitter when Google asks it to slow down."""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import requests
from pydantic import BaseModel, Field

from presence.adapters.google_client import builtin_client_config, has_builtin_client
from presence.tracker.conventions import (
    EVENT_STATUS,
    ConventionError,
    parse_status,
    show_status,
)
from presence.tracker.store import Candidate, Job, Tracker

SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE = "https://www.googleapis.com/drive/v3/files"
SCOPE_SHEETS = "https://www.googleapis.com/auth/spreadsheets"
SCOPE_FILES = "https://www.googleapis.com/auth/drive.file"
HEADER = [
    "ID",
    "Company",
    "Role",
    "Location",
    "Status",
    "Timeline",
    "Next action",
    "Notes",
    "Link",
    "Source",
    "Tier",
    "First seen",
]
SA_FILE = "google_service_account.json"
OAUTH_CLIENT = "google_oauth_client.json"
OAUTH_TOKEN = "google_oauth_token.json"
RECONNECT_FLAG = "google_reconnect"  # present = Google rejected the stored token
STATE_FILE = "sheet_sync.json"
RECONNECT_MSG = "Google needs you to reconnect"
SUCCESS_PAGE = (
    "Presence is connected to Google. You can close this tab and go back to Presence."
)
MAX_TRIES = 5
_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
_RATE_WORDS = ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded")


class SheetError(Exception):
    pass


class SyncResult(BaseModel):
    pulled: list[str] = Field(default_factory=list)
    pushed: int = 0
    added_from_sheet: int = 0
    issues: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        s = f"{len(self.pulled)} edit(s) pulled, {self.pushed} row(s) pushed"
        if self.added_from_sheet:
            s += f", {self.added_from_sheet} new from the sheet"
        return s + (f", {len(self.issues)} issue(s)" if self.issues else "")


def sheet_id_from(text: str) -> str:
    m = _ID_RE.search(text.strip())
    return m.group(1) if m else text.strip()


def _write_private(path: Path, text: str) -> None:
    path.write_text(text)
    os.chmod(path, 0o600)


# ------------------------------------------------------------------ credentials


def save_service_account(data: Path, raw: bytes) -> str:
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise SheetError("that file is not JSON") from exc
    if d.get("type") != "service_account" or not d.get("client_email"):
        raise SheetError("that is not a service-account key file")
    _write_private(data / SA_FILE, json.dumps(d))
    return str(d["client_email"])


def save_oauth_client(data: Path, raw: bytes) -> None:
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise SheetError("that file is not JSON") from exc
    if "installed" not in d:
        raise SheetError("that is not a Desktop-app OAuth client file (needs an 'installed' key)")
    _write_private(data / OAUTH_CLIENT, json.dumps(d))


def connection(data: Path) -> dict[str, Any]:
    """What the data folder holds: kind ('' / 'oauth' / 'service_account'),
    whether this build ships a Google client, whether the person uploaded one,
    and whether Google has asked for a fresh sign-in."""
    base = {
        "kind": "",
        "email": "",
        "builtin": has_builtin_client(),
        "has_client": (data / OAUTH_CLIENT).exists(),
        "needs_reconnect": False,
    }
    if (data / SA_FILE).exists():
        email = json.loads((data / SA_FILE).read_text()).get("client_email", "")
        return {**base, "kind": "service_account", "email": email}
    if (data / OAUTH_TOKEN).exists():
        return {
            **base,
            "kind": "oauth",
            "has_client": True,
            "needs_reconnect": (data / RECONNECT_FLAG).exists(),
        }
    return base


def disconnect(data: Path) -> None:
    for name in (SA_FILE, OAUTH_TOKEN, RECONNECT_FLAG):
        (data / name).unlink(missing_ok=True)


def mark_reconnect(data: Path | None) -> None:
    if data is not None:
        (data / RECONNECT_FLAG).write_text("")


def credentials(data: Path) -> Any:
    """Whichever credential the data folder holds; None when not connected."""
    if (data / SA_FILE).exists():
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(
            str(data / SA_FILE), scopes=[SCOPE_SHEETS, SCOPE_FILES]
        )
    if (data / OAUTH_TOKEN).exists():
        from google.oauth2.credentials import Credentials

        return Credentials.from_authorized_user_file(str(data / OAUTH_TOKEN), [SCOPE_FILES])
    return None


_oauth: dict[str, Any] = {"running": False, "done": False, "error": None}
_oauth_thread: threading.Thread | None = None
oauth_finish_lock = threading.Lock()  # the app creates the sheet once after sign-in


def client_config(data: Path) -> dict[str, Any]:
    """The built-in client wins; a source build falls back to the uploaded file."""
    cfg = builtin_client_config()
    if cfg is not None:
        return cfg
    client = data / OAUTH_CLIENT
    if not client.exists():
        raise SheetError(
            "Google sign-in isn't included in this build — add your own client file "
            "under Advanced first"
        )
    try:
        return json.loads(client.read_text())
    except ValueError as exc:
        raise SheetError("the saved client file is not JSON") from exc


def _flow_class() -> Any:
    from google_auth_oauthlib.flow import InstalledAppFlow

    return InstalledAppFlow


def start_oauth(data: Path) -> dict[str, Any]:
    """Google sign-in in the person's browser; the reply lands on 127.0.0.1.

    Scope is drive.file only: Presence can see the sheet it creates and nothing
    else in the person's Drive. Runs on a thread; poll oauth_status()."""
    global _oauth_thread
    cfg = client_config(data)
    if _oauth["running"]:
        return _oauth
    _oauth.update({"running": True, "done": False, "error": None})

    def worker() -> None:
        try:
            flow = _flow_class().from_client_config(cfg, scopes=[SCOPE_FILES])
            creds = flow.run_local_server(
                host="127.0.0.1",
                port=0,
                open_browser=True,
                timeout_seconds=300,
                success_message=SUCCESS_PAGE,
            )
            _write_private(data / OAUTH_TOKEN, creds.to_json())
            (data / RECONNECT_FLAG).unlink(missing_ok=True)
            _oauth["done"] = True
        except Exception as exc:
            _oauth["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        finally:
            _oauth["running"] = False

    _oauth_thread = threading.Thread(target=worker, daemon=True)
    _oauth_thread.start()
    return _oauth


def oauth_status(data: Path) -> dict[str, Any]:
    return {**_oauth, "connected": (data / OAUTH_TOKEN).exists()}


# ------------------------------------------------------------------ API client


class Client(Protocol):
    def create(self, title: str, tab: str) -> tuple[str, str]: ...
    def share(self, file_id: str, email: str) -> None: ...
    def info(self, sheet_id: str) -> tuple[str, list[str]]: ...
    def read(self, sheet_id: str, rng: str) -> list[list[str]]: ...
    def write(self, sheet_id: str, rng: str, values: list[list[str]]) -> None: ...
    def clear(self, sheet_id: str, rng: str) -> None: ...


def _sleep(seconds: float) -> None:  # swapped out by tests
    time.sleep(seconds)


def _is_rate_limited(status: int, text: str) -> bool:
    return status == 429 or (status == 403 and any(w in text for w in _RATE_WORDS))


class SheetClient:
    """Sheets + Drive over plain HTTP; one bearer token from google-auth.

    Retries when Google asks it to slow down (429, or 403 rate-limit reasons)
    with exponential backoff and jitter, at most MAX_TRIES attempts. A token
    Google no longer accepts (invalid_grant, or a 401) becomes a plain
    "reconnect" error and, when the data folder is known, a flag the setup
    page turns into a Reconnect Google button."""

    def __init__(self, creds: Any, data: Path | None = None):
        self.creds = creds
        self.data = data

    def _headers(self) -> dict[str, str]:
        from google.auth.transport.requests import Request

        if not self.creds.valid:
            try:
                self.creds.refresh(Request())
            except Exception as exc:
                if "invalid_grant" in str(exc):
                    mark_reconnect(self.data)
                    raise SheetError(RECONNECT_MSG) from exc
                raise SheetError(f"Google: {type(exc).__name__}: {str(exc)[:120]}") from exc
        return {"Authorization": f"Bearer {self.creds.token}"}

    def _req(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        for attempt in range(MAX_TRIES):
            try:
                r = requests.request(method, url, headers=self._headers(), timeout=30, **kw)
            except SheetError:
                raise
            except Exception as exc:
                raise SheetError(f"Google: {type(exc).__name__}: {str(exc)[:120]}") from exc
            if _is_rate_limited(r.status_code, r.text) and attempt < MAX_TRIES - 1:
                _sleep(min(2**attempt, 16) + random.uniform(0, 1))
                continue
            break
        if r.status_code == 401:
            mark_reconnect(self.data)
            raise SheetError(RECONNECT_MSG)
        if r.status_code >= 400:
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                msg = r.text[:200]
            if _is_rate_limited(r.status_code, r.text):
                msg = "Google is busy — try again in a minute"
            raise SheetError(f"Google API {r.status_code}: {msg}")
        return r.json() if r.text.strip() else {}

    def create(self, title: str, tab: str = "Tracker") -> tuple[str, str]:
        body = {
            "properties": {"title": title},
            "sheets": [{"properties": {"title": tab, "gridProperties": {"frozenRowCount": 1}}}],
        }
        try:
            d = self._req("POST", SHEETS, json=body)
        except SheetError as exc:
            if "403" in str(exc):
                who = connection(self.data).get("email") if self.data else ""
                if who:
                    raise SheetError("Google no longer lets a service account own a new sheet. "
                                     "Make a blank sheet in your own Drive, share it as editor "
                                     f"with {who}, then connect it here.") from exc
                raise SheetError(f"{exc} — creating a sheet needs the Google Drive API enabled "
                                 "on the project this account belongs to") from exc
            raise
        return d["spreadsheetId"], d["spreadsheetUrl"]

    def share(self, file_id: str, email: str) -> None:
        self._req(
            "POST",
            f"{DRIVE}/{file_id}/permissions",
            params={"sendNotificationEmail": "false"},
            json={"role": "writer", "type": "user", "emailAddress": email},
        )

    def delete(self, file_id: str) -> None:
        self._req("DELETE", f"{DRIVE}/{file_id}")

    def info(self, sheet_id: str) -> tuple[str, list[str]]:
        d = self._req(
            "GET",
            f"{SHEETS}/{sheet_id}",
            params={"fields": "spreadsheetUrl,sheets.properties.title"},
        )
        return d.get("spreadsheetUrl", ""), [s["properties"]["title"] for s in d.get("sheets", [])]

    def read(self, sheet_id: str, rng: str) -> list[list[str]]:
        d = self._req("GET", f"{SHEETS}/{sheet_id}/values/{quote(rng, safe='')}")
        return [[str(c) for c in row] for row in d.get("values", [])]

    def write(self, sheet_id: str, rng: str, values: list[list[str]]) -> None:
        self._req(
            "PUT",
            f"{SHEETS}/{sheet_id}/values/{quote(rng, safe='')}",
            params={"valueInputOption": "RAW"},
            json={"values": values},
        )

    def clear(self, sheet_id: str, rng: str) -> None:
        self._req("POST", f"{SHEETS}/{sheet_id}/values/{quote(rng, safe='')}:clear")


def rng(tab: str, a1: str) -> str:
    return f"'{tab}'!{a1}"


# ------------------------------------------------------------------ sync


def _human(job: Job) -> dict[str, str]:
    return {
        "Status": show_status(job.status),
        "Next action": str(job.extra.get("next_action", "")),
        "Notes": job.note,
    }


def _row(tracker: Tracker, job: Job) -> list[str]:
    h = _human(job)
    return [
        str(job.id),
        job.company,
        job.title,
        job.location,
        h["Status"],
        tracker.timeline(job.id),
        h["Next action"],
        h["Notes"],
        job.url,
        job.source,
        job.tier,
        job.created_at[:10],
    ]


def check_header(rows: list[list[str]]) -> None:
    """Refuse to overwrite a sheet that holds someone's other columns."""
    if not rows:
        return
    header = [c.strip() for c in rows[0]] + [""] * len(HEADER)
    if header[: len(HEADER)] != HEADER and any(c.strip() for r in rows for c in r):
        raise SheetError(
            "this sheet already has different columns — Presence writes its own; "
            "use Import to bring those rows in, or connect an empty sheet"
        )


def connect_existing(client: Client, sheet_id: str) -> tuple[str, str]:
    """Validate a sheet the person already has; returns (url, tab)."""
    url, tabs = client.info(sheet_id)
    if not tabs:
        raise SheetError("that spreadsheet has no sheets")
    tab = tabs[0]
    check_header(client.read(sheet_id, rng(tab, "A1:L")))
    return url, tab


def _apply_status(tracker: Tracker, job: Job, label: str, today: date, out: SyncResult) -> None:
    new = parse_status(label)
    if new is None:
        out.issues.append(f"{job.company} — {job.title}: unknown status '{label}'")
        return
    if new == job.status:
        return
    try:
        kind = next((k for k, v in EVENT_STATUS.items() if v == new), None)
        if kind:
            tracker.record_event(job.id, kind, today)
        else:
            tracker.set_status(job.id, new)
        out.pulled.append(
            f"{job.company} — {job.title}: {show_status(job.status)} → {show_status(new)}"
        )
    except ConventionError as exc:
        out.issues.append(f"{job.company} — {job.title}: {exc}")


def sync(
    tracker: Tracker,
    client: Client,
    sheet_id: str,
    tab: str,
    state_path: Path,
    today: date | None = None,
) -> SyncResult:
    """Pull the person's edits, then push every row. Idempotent when nothing changed."""
    today = today or date.today()
    out = SyncResult()
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    snapshot: dict[str, dict[str, str]] = state.get("snapshot", {})
    rows = client.read(sheet_id, rng(tab, "A1:L"))
    check_header(rows)
    jobs = {j.id: j for j in tracker.list()}
    for raw in rows[1:]:
        rec = dict(zip(HEADER, (raw + [""] * len(HEADER))[: len(HEADER)], strict=True))
        if not rec["ID"].strip():
            if rec["Company"].strip() and rec["Role"].strip():
                job, created = tracker.ingest(
                    Candidate(
                        company=rec["Company"].strip(),
                        title=rec["Role"].strip(),
                        location=rec["Location"].strip(),
                        url=rec["Link"].strip(),
                        source="sheet",
                        note=rec["Notes"],
                    ),
                    today=today,
                )
                if created:
                    out.added_from_sheet += 1
                    if rec["Next action"].strip():
                        tracker.set_extra(job.id, next_action=rec["Next action"].strip())
                    if rec["Status"].strip():
                        _apply_status(tracker, job, rec["Status"], today, out)
            continue
        try:
            jid = int(rec["ID"])
        except ValueError:
            out.issues.append(f"row with ID '{rec['ID']}' is not a Presence row")
            continue
        job = jobs.get(jid)
        if job is None:
            out.issues.append(f"ID {jid} is not in the tracker any more")
            continue
        prev = snapshot.get(str(jid), _human(job))
        if rec["Status"].strip() != prev["Status"]:
            _apply_status(tracker, job, rec["Status"], today, out)
        if rec["Next action"] != prev["Next action"]:
            tracker.set_extra(jid, next_action=rec["Next action"])
            out.pulled.append(f"{job.company} — {job.title}: next action updated")
        if rec["Notes"] != prev["Notes"]:
            tracker.set_note(jid, rec["Notes"])
            out.pulled.append(f"{job.company} — {job.title}: notes updated")
    all_jobs = sorted(tracker.list(), key=lambda j: (j.created_at, j.id), reverse=True)
    values = [HEADER] + [_row(tracker, j) for j in all_jobs]
    client.clear(sheet_id, rng(tab, "A2:L"))
    client.write(sheet_id, rng(tab, f"A1:L{len(values)}"), values)
    out.pushed = len(all_jobs)
    state = {
        "snapshot": {str(j.id): _human(j) for j in all_jobs},
        "last": {
            "at": datetime.now().isoformat(timespec="seconds"),
            "pulled": len(out.pulled),
            "pushed": out.pushed,
            "issues": out.issues,
        },
    }
    _write_private(state_path, json.dumps(state, ensure_ascii=False))
    return out
