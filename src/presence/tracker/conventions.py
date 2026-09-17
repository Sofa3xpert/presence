"""Tracker conventions — the rules that keep a large tracker sane,
as code. Status vocabulary, the event timeline, and the normalisers used for
duplicate detection. Pure functions; the store applies them."""

from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher

STATUSES = ("to_apply", "applied", "assessment", "interview", "interview_done",
            "rejected", "ignored")
OPEN_STATUSES = frozenset({"to_apply", "applied", "assessment", "interview", "interview_done"})
DEAD_STATUSES = frozenset({"rejected", "ignored"})

# An event of this kind moves the job to this status. "note" changes nothing.
EVENT_STATUS = {
    "applied": "applied",
    "assessment": "assessment",
    "interview": "interview",
    "interview_done": "interview_done",
    "rejected": "rejected",
    "ignored": "ignored",
}
# Neutral kinds appear on the timeline (except notes) and change no status.
NEUTRAL_KINDS = ("note", "messaged")
EVENT_KINDS = tuple(EVENT_STATUS) + NEUTRAL_KINDS


class ConventionError(Exception):
    """A write that would break a tracker convention."""


def fmt_date(d: date) -> str:
    return f"{d.day} {d:%b}"


def render_timeline(events: list[tuple[str, date, str]]) -> str:
    """Events (kind, date, detail), in order -> '13 Aug → CCAT 1 Sep → rejected 8 Sep'.

    A leading applied event is a bare date; later events read '→ <label> <date>';
    a timeline with no applied event starts straight with the label ('rejected 22 Aug'),
    which by convention means the application date is unknown. Notes never appear."""
    parts: list[str] = []
    for kind, when, detail in events:
        if kind == "note":
            continue
        if kind == "applied" and not parts:
            parts.append(fmt_date(when))
            continue
        label = detail if (kind == "assessment" and detail) else kind.replace("_", " ")
        parts.append(f"{label} {fmt_date(when)}")
    return " → ".join(parts)


_DATE_RE = re.compile(r"^(\d{1,2}) ([A-Za-z]{3})$")
_LABELLED_RE = re.compile(r"^(.*?)\s+(\d{1,2} [A-Za-z]{3})$")
_LABEL_KINDS = (
    ("rejected", "rejected"), ("refused", "rejected"), ("interview done", "interview_done"),
    ("interview", "interview"), ("applied", "applied"), ("re-applied", "applied"),
    ("messaged", "messaged"),  # without this a sheet round-trip would turn it into an assessment
)


def _parse_date(text: str, year: int) -> date | None:
    m = _DATE_RE.match(text.strip())
    if not m:
        return None
    try:
        return date(year, _month(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _month(abbr: str) -> int:
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    return months.index(abbr.lower()[:3]) + 1


def parse_timeline(text: str, year: int) -> list[tuple[str, date, str]]:
    """Inverse of render_timeline for imported data; best-effort, never raises.
    Unrecognised labels become assessment events carrying the label as detail
    (that is how 'CCAT 1 Sep' round-trips)."""
    events: list[tuple[str, date, str]] = []
    for seg in (s.strip() for s in text.split("→")):
        seg = re.sub(r"\(.*?\)", "", seg).strip()
        if not seg:
            continue
        when = _parse_date(seg, year)
        if when is not None:
            events.append(("applied", when, ""))
            continue
        m = _LABELLED_RE.match(seg)
        if not m:
            continue
        label, when = m.group(1).strip(), _parse_date(m.group(2), year)
        if when is None:
            continue
        kind = next((k for word, k in _LABEL_KINDS if label.lower().startswith(word)), None)
        if kind:
            events.append((kind, when, ""))
        else:
            events.append(("assessment", when, label))
    return events


# ---------------------------------------------------------------- normalisers

_STOP_TOKENS = {
    "graduate", "grad", "junior", "new", "entry", "level", "2025", "2026", "2027",
    "programme", "program", "scheme", "london", "uk", "remote", "hybrid", "full",
    "time", "intern", "internship", "the", "of", "and", "a", "an", "&", "i", "ii", "iii",
}
_COMPANY_SUFFIXES = (" technologies", " technology", " ltd", " plc", " inc", " uk & ireland",
                     " uk and ireland", " group", " limited", " company")


def norm_company(name: str) -> str:
    c = re.sub(r"[.,()]", " ", name.lower().strip())
    for suffix in _COMPANY_SUFFIXES:
        c = c.replace(suffix, " ")
    return re.sub(r"\s+", " ", c).strip()


def norm_title(title: str) -> str:
    words = re.sub(r"[^\w\s]", " ", title.lower()).split()
    return " ".join(w for w in words if w not in _STOP_TOKENS)


def title_similarity(a: str, b: str) -> float:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb)
    return max(SequenceMatcher(None, na, nb).ratio(), jaccard)


def link_key(url: str) -> str:
    """A stable identifier for a posting URL, so the same job seen through two
    boards (or with tracking parameters) dedupes. Empty when there is no URL."""
    u = url.strip().lower()
    if not u:
        return ""
    bare = re.sub(r"[?#].*$", "", u).rstrip("/")
    for pattern, prefix in (
        (r"linkedin\.com/jobs/view/(\d+)", "li:"),
        (r"jk=([0-9a-f]{8,})", "indeed:"),
        (r"(?:jobs?|postings?|careers?|view|openings)[/=](\d{5,})", "id:"),
        (r"(\d{7,})", "num:"),
    ):
        m = re.search(pattern, u if prefix == "indeed:" else bare)
        if m:
            return prefix + m.group(1)
    return "url:" + bare


# ---------------------------------------------------------------- status labels

_STATUS_ALIASES = {
    "to apply": "to_apply", "toapply": "to_apply", "new": "to_apply", "open": "to_apply",
    "applied": "applied", "submitted": "applied",
    "assessment": "assessment", "oa": "assessment", "test": "assessment",
    "screening": "assessment",
    "interview": "interview", "interviewing": "interview",
    "interview done": "interview_done", "interviewed": "interview_done",
    "rejected": "rejected", "reject": "rejected", "refused": "rejected",
    "ignored": "ignored", "ignore": "ignored", "skip": "ignored", "skipped": "ignored",
    "not applying": "ignored", "outdated": "ignored", "expired": "ignored", "closed": "ignored",
    "withdrawn": "ignored", "not eligible": "ignored", "ineligible": "ignored",
}


def parse_status(label: str) -> str | None:
    """A person's status label → canonical status, else None.
    'Interview done', 'to-apply' and 'Ignored (under-qualified)' all resolve."""
    key = re.sub(r"\(.*?\)", " ", label)
    key = re.sub(r"[\s_\-]+", " ", key.strip().lower()).strip()
    return _STATUS_ALIASES.get(key)


def status_reason(label: str) -> str:
    """The bracketed part of a label, e.g. 'under-qualified' — worth keeping as a note."""
    m = re.search(r"\((.*?)\)", label)
    return m.group(1).strip() if m else ""


def show_status(status: str) -> str:
    return status.replace("_", " ")
