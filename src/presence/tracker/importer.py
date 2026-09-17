"""Bring an existing tracker in — a CSV export, or rows read from a sheet.
Column names are matched loosely (Company, Role/Title, Link/URL, Status,
Applied/Timeline, Channel/Source, Location, Tier, Next action, Notes); any
other column is kept in `extra`. Duplicates are skipped by the store's rules."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from pydantic import BaseModel, Field

from presence.tracker.conventions import (
    ConventionError,
    parse_status,
    parse_timeline,
    status_reason,
)
from presence.tracker.store import Candidate, Tracker

ALIASES = {
    "company": "company",
    "employer": "company",
    "organisation": "company",
    "organization": "company",
    "role": "title",
    "title": "title",
    "job title": "title",
    "position": "title",
    "location": "location",
    "link": "url",
    "url": "url",
    "job link": "url",
    "status": "status",
    "applied": "timeline",
    "timeline": "timeline",
    "channel": "source",
    "source": "source",
    "tier": "tier",
    "next action": "next_action",
    "notes": "note",
    "note": "note",
}


class ImportReport(BaseModel):
    created: int = 0
    duplicates: int = 0
    skipped: int = 0
    issues: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        s = f"{self.created} added, {self.duplicates} already tracked, {self.skipped} skipped"
        return s + (f", {len(self.issues)} issue(s)" if self.issues else "")


def column_map(header: Sequence[str]) -> list[str | None]:
    return [ALIASES.get(h.strip().lower()) for h in header]


def import_rows(
    tracker: Tracker,
    header: Sequence[str],
    rows: Iterable[Sequence[str]],
    year: int,
    source_default: str = "import",
) -> ImportReport:
    rep = ImportReport()
    keys = column_map(header)
    for raw in rows:
        cells = [str(c) for c in raw] + [""] * len(header)
        rec: dict[str, str] = {}
        extra: dict[str, str] = {}
        for h, k, v in zip(header, keys, cells, strict=False):
            v = v.strip()
            if k and v:
                rec.setdefault(k, v)
            elif not k and h.strip() and v:
                extra[h.strip()] = v
        if not (rec.get("company") and rec.get("title")):
            rep.skipped += 1
            continue
        job, created = tracker.ingest(
            Candidate(
                company=rec["company"],
                title=rec["title"],
                location=rec.get("location", ""),
                url=rec.get("url", ""),
                source=rec.get("source") or source_default,
                tier=rec.get("tier", ""),
                note=rec.get("note", ""),
            )
        )
        if not created:
            rep.duplicates += 1
            continue
        rep.created += 1
        for kind, when, detail in parse_timeline(rec.get("timeline", ""), year):
            try:
                tracker.record_event(job.id, kind, when, detail)
            except ConventionError as exc:
                rep.issues.append(f"{rec['company']}: {exc}")
                break
        label = rec.get("status", "")
        if label:
            status = parse_status(label)
            if status is None:
                rep.issues.append(
                    f"{rec['company']}: unknown status '{label}' — "
                    f"left as {tracker.get(job.id).status}"
                )
            elif tracker.get(job.id).status != status:
                tracker.set_status(job.id, status)
            reason = status_reason(label)
            if status is not None and reason:
                note = tracker.get(job.id).note
                tracker.set_note(job.id, f"{note} · {reason}" if note else reason)
        if rec.get("next_action"):
            extra["next_action"] = rec["next_action"]
        if extra:
            tracker.set_extra(job.id, **extra)
    return rep
