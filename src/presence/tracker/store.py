"""The tracker store — SQLite, stdlib only, the conventions applied on every write.

Core fields are what the engine interprets; everything a person wants beyond
them lives in `extra` (JSON the engine stores and returns untouched). The
timeline is not a column: it is rendered from the events table on demand."""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from presence.tracker.conventions import (
    DEAD_STATUSES,
    EVENT_KINDS,
    EVENT_STATUS,
    NEUTRAL_KINDS,
    STATUSES,
    ConventionError,
    link_key,
    norm_company,
    norm_title,
    render_timeline,
    title_similarity,
)

MIGRATIONS: list[tuple[str, str]] = [
    ("001_core", """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            company TEXT NOT NULL,
            company_norm TEXT NOT NULL,
            title TEXT NOT NULL,
            title_norm TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            link_key TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'to_apply',
            tier TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            extra TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX jobs_link_key ON jobs(link_key);
        CREATE INDEX jobs_company_norm ON jobs(company_norm);
        CREATE INDEX jobs_status ON jobs(status);
        CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            date TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX events_job ON events(job_id);
        CREATE TABLE cooldowns (
            company_norm TEXT PRIMARY KEY,
            until TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT ''
        );
    """),
]


class Candidate(BaseModel):
    """What a connector hands the tracker: a posting, not yet a tracked job."""

    company: str
    title: str
    location: str = ""
    url: str = ""
    source: str = ""
    tier: str = ""
    note: str = ""


class Job(BaseModel):
    id: int
    company: str
    title: str
    location: str
    url: str
    source: str
    status: str
    tier: str
    note: str
    extra: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Tracker:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    # ---------------------------------------------------------- schema

    def migrate(self) -> list[str]:
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version TEXT PRIMARY KEY, applied_at TEXT)"
        )
        done = {r["version"] for r in self.con.execute("SELECT version FROM schema_version")}
        applied = []
        for version, sql in MIGRATIONS:
            if version in done:
                continue
            with self.con:
                self.con.executescript(sql)
                self.con.execute(
                    "INSERT INTO schema_version VALUES (?, ?)", (version, _now())
                )
            applied.append(version)
        return applied

    # ---------------------------------------------------------- reading

    def _job(self, row: sqlite3.Row) -> Job:
        d = dict(row)
        d["extra"] = json.loads(d.pop("extra") or "{}")
        for k in ("company_norm", "title_norm", "link_key"):
            d.pop(k, None)
        return Job(**d)

    def get(self, job_id: int) -> Job:
        row = self.con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"no job {job_id}")
        return self._job(row)

    def list(self, status: str | None = None) -> list[Job]:
        if status is None:
            rows = self.con.execute("SELECT * FROM jobs ORDER BY id")
        else:
            rows = self.con.execute("SELECT * FROM jobs WHERE status = ? ORDER BY id", (status,))
        return [self._job(r) for r in rows]

    def events(self, job_id: int) -> list[tuple[str, date, str]]:
        rows = self.con.execute(
            "SELECT kind, date, detail FROM events WHERE job_id = ? ORDER BY date, id", (job_id,)
        )
        return [(r["kind"], date.fromisoformat(r["date"]), r["detail"]) for r in rows]

    def timeline(self, job_id: int) -> str:
        return render_timeline(self.events(job_id))

    # ---------------------------------------------------------- intake

    def find_duplicate(self, cand: Candidate) -> Job | None:
        """Same posting already tracked? Precision first — a real posting must
        never be swallowed: the same link key is a duplicate; an identical
        normalised title at the same company is a duplicate only when one side
        has no link (two different links are two postings, whatever the titles).
        Fuzzy similarity is not a merge criterion — see find_similar."""
        key = link_key(cand.url)
        if key:
            row = self.con.execute("SELECT * FROM jobs WHERE link_key = ?", (key,)).fetchone()
            if row is not None:
                return self._job(row)
        cnorm, tnorm = norm_company(cand.company), norm_title(cand.title)
        for row in self.con.execute("SELECT * FROM jobs WHERE company_norm = ?", (cnorm,)):
            if key and row["link_key"] and row["link_key"] != key:
                continue
            if row["title_norm"] == tnorm:
                return self._job(row)
        return None

    def find_similar(self, cand: Candidate, threshold: float = 0.85) -> list[Job]:
        """Tracked jobs at the same company with a similar title — a hint for
        agents and briefs ("looks like a repost of #12"), never a merge."""
        cnorm = norm_company(cand.company)
        return [self._job(r) for r in
                self.con.execute("SELECT * FROM jobs WHERE company_norm = ?", (cnorm,))
                if title_similarity(cand.title, r["title"]) >= threshold]

    def ingest(self, cand: Candidate, today: date | None = None) -> tuple[Job, bool]:
        """Track a candidate posting. Returns (job, created). Never duplicates;
        a company under cooldown is stored as ignored so it stays visible."""
        existing = self.find_duplicate(cand)
        if existing is not None:
            return existing, False
        today = today or date.today()
        cnorm = norm_company(cand.company)
        now = _now()
        with self.con:
            cur = self.con.execute(
                "INSERT INTO jobs (company, company_norm, title, title_norm, location, url, "
                "link_key, source, status, tier, note, extra, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'to_apply', ?, ?, '{}', ?, ?)",
                (cand.company, cnorm, cand.title, norm_title(cand.title), cand.location,
                 cand.url, link_key(cand.url), cand.source, cand.tier, cand.note, now, now),
            )
        job_id = int(cur.lastrowid)
        until = self.cooling_until(cand.company, today)
        if until is not None:
            self.record_event(job_id, "ignored", today, f"cooldown until {until.isoformat()}")
        return self.get(job_id), True

    # ---------------------------------------------------------- state changes

    def record_event(self, job_id: int, kind: str, when: date, detail: str = "") -> Job:
        """Append an event and move status accordingly. Dead jobs accept only
        notes — a rejection is never silently undone by a later confirmation."""
        if kind not in EVENT_KINDS:
            raise ConventionError(f"unknown event kind '{kind}'; use one of {EVENT_KINDS}")
        job = self.get(job_id)
        if job.status in DEAD_STATUSES and kind not in NEUTRAL_KINDS:
            raise ConventionError(
                f"job {job_id} is {job.status}; only notes may be added to a closed job"
            )
        with self.con:
            self.con.execute(
                "INSERT INTO events (job_id, kind, date, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, kind, when.isoformat(), detail, _now()),
            )
            if kind in EVENT_STATUS:
                self.con.execute(
                    "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                    (EVENT_STATUS[kind], _now(), job_id),
                )
        return self.get(job_id)

    def set_status(self, job_id: int, status: str) -> Job:
        """Direct status set for corrections; normal flow is record_event."""
        if status not in STATUSES:
            raise ConventionError(f"unknown status '{status}'; use one of {STATUSES}")
        with self.con:
            self.con.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), job_id)
            )
        return self.get(job_id)

    def set_note(self, job_id: int, note: str) -> Job:
        with self.con:
            self.con.execute(
                "UPDATE jobs SET note = ?, updated_at = ? WHERE id = ?", (note, _now(), job_id)
            )
        return self.get(job_id)

    def set_extra(self, job_id: int, **fields: Any) -> Job:
        """Merge user-defined fields. The engine never reads these."""
        job = self.get(job_id)
        merged = {**job.extra, **fields}
        with self.con:
            self.con.execute(
                "UPDATE jobs SET extra = ?, updated_at = ? WHERE id = ?",
                (json.dumps(merged, ensure_ascii=False), _now(), job_id),
            )
        return self.get(job_id)

    # ---------------------------------------------------------- cooldowns

    def add_cooldown(self, company: str, until: date, reason: str = "") -> None:
        with self.con:
            self.con.execute(
                "INSERT OR REPLACE INTO cooldowns VALUES (?, ?, ?)",
                (norm_company(company), until.isoformat(), reason),
            )

    def cooling_until(self, company: str, on: date) -> date | None:
        row = self.con.execute(
            "SELECT until FROM cooldowns WHERE company_norm = ?", (norm_company(company),)
        ).fetchone()
        if row is None:
            return None
        until = date.fromisoformat(row["until"])
        return until if until > on else None

    # ---------------------------------------------------------- export / backup

    def export_csv(self, path: Path | str) -> int:
        cols = ["id", "company", "title", "location", "url", "source", "status", "tier",
                "timeline", "note", "extra"]
        jobs = self.list()
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for j in jobs:
                w.writerow([j.id, j.company, j.title, j.location, j.url, j.source, j.status,
                            j.tier, self.timeline(j.id), j.note, json.dumps(j.extra)])
        return len(jobs)

    def backup(self, path: Path | str) -> Path:
        """Consistent copy of the database (SQLite online backup); restore = copy it back."""
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(dest) as target:
            self.con.backup(target)
        return dest

    def close(self) -> None:
        self.con.close()
