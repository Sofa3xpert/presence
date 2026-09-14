"""Google Sheet mirror of the tracker.

SQLite stays the source of truth; the sheet is where a person edits. Presence
owns the columns it fills (identity, timeline, link, source); the person owns
Status, Next action and Notes. A sync pulls those edits first, then pushes
every row back, so the sheet always ends up looking like the tracker. A row a
person adds by hand (no ID) becomes a tracked job.

Two ways in: a service account (developer path: works with any sheet shared
with it) or the person's own Google account via OAuth on the loopback address,
limited to files Presence created — the narrow drive.file scope."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import requests
from pydantic import BaseModel, Field

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
STATE_FILE = "sheet_sync.json"
_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")


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
    if (data / SA_FILE).exists():
        email = json.loads((data / SA_FILE).read_text()).get("client_email", "")
        return {
            "kind": "service_account",
            "email": email,
            "has_client": (data / OAUTH_CLIENT).exists(),
        }
    if (data / OAUTH_TOKEN).exists():
        return {"kind": "oauth", "email": "", "has_client": True}
    return {"kind": "", "email": "", "has_client": (data / OAUTH_CLIENT).exists()}


def disconnect(data: Path) -> None:
    for name in (SA_FILE, OAUTH_TOKEN):
        (data / name).unlink(missing_ok=True)


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


def start_oauth(data: Path) -> dict[str, Any]:
    """Google sign-in in the person's browser; the reply lands on 127.0.0.1."""
    client = data / OAUTH_CLIENT
    if not client.exists():
        raise SheetError("add an OAuth client file first")
    if _oauth["running"]:
        return _oauth
    _oauth.update({"running": True, "done": False, "error": None})

    def worker() -> None:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(str(client), scopes=[SCOPE_FILES])
            creds = flow.run_local_server(
                host="127.0.0.1", port=0, open_browser=True, timeout_seconds=300
            )
            _write_private(data / OAUTH_TOKEN, creds.to_json())
            _oauth["done"] = True
        except Exception as exc:
            _oauth["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        finally:
            _oauth["running"] = False

    threading.Thread(target=worker, daemon=True).start()
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


class SheetClient:
    """Sheets + Drive over plain HTTP; one bearer token from google-auth."""

    def __init__(self, creds: Any):
        self.creds = creds

    def _headers(self) -> dict[str, str]:
        from google.auth.transport.requests import Request

        if not self.creds.valid:
            self.creds.refresh(Request())
        return {"Authorization": f"Bearer {self.creds.token}"}

    def _req(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        try:
            r = requests.request(method, url, headers=self._headers(), timeout=30, **kw)
        except Exception as exc:
            raise SheetError(f"Google: {type(exc).__name__}: {str(exc)[:120]}") from exc
        if r.status_code >= 400:
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                msg = r.text[:200]
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
                raise SheetError(f"{exc} — creating a sheet needs the Google Drive API enabled "
                                 "on the project this account belongs to (the Sheets API alone "
                                 "only reads and writes existing sheets)") from exc
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
