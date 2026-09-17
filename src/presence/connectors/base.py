"""Connector interface and registry — job sources behind one shape.

Every source is an interface its provider publishes: a job-board API a
company exposes on purpose (Greenhouse, Lever, Ashby, Workable,
SmartRecruiters), or — next — the customer's own alert emails. Presence never
scrapes a website (charter rule 1). A connector turns a Source (one company's
board, one mailbox) into Postings; the runner applies the customer's filters.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel, Field

from presence.core.config import SearchConfig, SourceEntry
from presence.tracker.conventions import link_key
from presence.tracker.store import Candidate


class ConnectorError(Exception):
    """A source failed; the run continues with the others."""


class Posting(BaseModel):
    """One job as a source reports it; `candidate` is what the tracker ingests."""

    company: str
    title: str
    location: str = ""
    url: str = ""
    source: str = ""
    posted: str = ""  # ISO date when the source knows it
    description: str = ""  # the job's text as plain text, when the listing carries it
    description_ref: str = ""  # the board's posting id, when the text needs its own GET

    @property
    def candidate(self) -> Candidate:
        return Candidate(company=self.company, title=self.title, location=self.location,
                         url=self.url, source=self.source)


class Connector(Protocol):
    provider: str

    def fetch(self, source: SourceEntry) -> list[Posting]: ...


REGISTRY: dict[str, Callable[..., Connector]] = {}


def register(provider: str) -> Callable[[type], type]:
    def deco(cls: type) -> type:
        REGISTRY[provider] = cls
        return cls
    return deco


def create(provider: str, **config: Any) -> Connector:
    try:
        factory = REGISTRY[provider]
    except KeyError:
        raise ConnectorError(
            f"unknown provider '{provider}'; available: {sorted(REGISTRY)}") from None
    return factory(**config)


# ------------------------------------------------------------- filtering

def matches(p: Posting, search: SearchConfig, today: date | None = None) -> bool:
    """The customer's filters, applied to a posting from any source."""
    title, loc, company = p.title.lower(), p.location.lower(), p.company.lower()
    if any(b.lower() in company for b in search.blocklist if b.strip()):
        return False
    if search.title_exclude and any(x.lower() in title for x in search.title_exclude):
        return False
    if search.title_include and not any(x.lower() in title for x in search.title_include):
        return False
    if search.locations:
        wanted = [w.lower() for w in search.locations]
        remote = search.remote_ok and "remote" in loc
        if not remote and not any(w in loc for w in wanted):
            return False
    if p.posted and search.freshness_hours:
        try:
            posted = date.fromisoformat(p.posted[:10])
            cutoff = (today or date.today()) - timedelta(hours=search.freshness_hours)
            if posted < cutoff:
                return False
        except ValueError:
            pass  # unknown date format: don't drop on freshness
    return True


def run_sources(sources: list[SourceEntry], search: SearchConfig, seen: set[str] | None = None,
                connectors: dict[str, Connector] | None = None,
                ) -> tuple[list[Posting], dict[str, str]]:
    """Fetch every enabled source, filter, dedupe within the run, skip seen link
    keys. Returns postings and a source-id → error map; never raises for one
    bad source."""
    seen = seen if seen is not None else set()
    connectors = connectors if connectors is not None else {}
    postings: list[Posting] = []
    errors: dict[str, str] = {}
    keys: set[str] = set()
    for src in sources:
        if not src.enabled:
            continue
        try:
            conn = connectors.get(src.provider) or create(src.provider)
            connectors[src.provider] = conn
            found = conn.fetch(src)
        except ConnectorError as exc:
            errors[src.id] = str(exc)
            continue
        for p in found:
            key = link_key(p.url) or f"title:{p.company.lower()}|{p.title.lower()}"
            if key in seen or key in keys or not matches(p, search):
                continue
            keys.add(key)
            postings.append(p)
    return postings, errors


def fetch_description(p: Posting, sources: list[SourceEntry],
                      connectors: dict[str, Connector] | None = None) -> str:
    """The full text of one posting whose board lists jobs without it
    (SmartRecruiters): one GET for this posting, made only once the tracker has
    created the job — never inside a listing loop. Raises ConnectorError."""
    if p.description or not p.description_ref:
        return p.description
    same = [s for s in sources if s.enabled and s.provider == p.source]
    src = next((s for s in same if s.label == p.company), same[0] if len(same) == 1 else None)
    if src is None:
        raise ConnectorError(f"no source for {p.company} on {p.source}")
    connectors = connectors if connectors is not None else {}
    conn = connectors.get(p.source) or create(p.source)
    getter = getattr(conn, "fetch_description", None)
    return str(getter(src, p.description_ref) or "") if getter else ""


def _iso(value: Any) -> str:
    if isinstance(value, int | float):  # epoch millis (Lever)
        return datetime.fromtimestamp(value / 1000).date().isoformat()
    return str(value or "")[:10]


__all__ = ["Connector", "ConnectorError", "Field", "Posting", "REGISTRY", "create",
           "fetch_description", "matches", "register", "run_sources"]
