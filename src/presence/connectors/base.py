"""Connector interface and registry — job sources behind one shape.

A connector turns a SearchQuery into Postings. v1 ships one connector,
"linkedin" (library-based search). Adding a source is additive: implement
the protocol, register it below, nothing else changes. Planned next:
"email_alerts" — the customer's own saved-search alert emails parsed from a
read-only mailbox (the same intake the author's pipeline already runs).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel

from presence.core.config import SearchConfig
from presence.tracker.conventions import link_key
from presence.tracker.store import Candidate


class ConnectorError(Exception):
    """A source failed; the run continues with the others."""


class SearchQuery(BaseModel):
    term: str
    location: str = ""
    hours_old: int = 30
    limit: int = 20


class Posting(BaseModel):
    """One job as a source reports it; `candidate` is what the tracker ingests."""

    company: str
    title: str
    location: str = ""
    url: str = ""
    source: str = ""
    posted: str = ""  # ISO date when the source knows it
    description: str = ""

    @property
    def candidate(self) -> Candidate:
        return Candidate(company=self.company, title=self.title, location=self.location,
                         url=self.url, source=self.source)


class Connector(Protocol):
    name: str

    def search(self, query: SearchQuery) -> list[Posting]: ...


REGISTRY: dict[str, Callable[..., Connector]] = {}


def register(name: str) -> Callable[[type], type]:
    def deco(cls: type) -> type:
        REGISTRY[name] = cls
        return cls
    return deco


def create(name: str, **config: Any) -> Connector:
    try:
        factory = REGISTRY[name]
    except KeyError:
        raise ConnectorError(f"unknown connector '{name}'; available: {sorted(REGISTRY)}") from None
    return factory(**config)


# ------------------------------------------------------------- search runs

def build_queries(search: SearchConfig, max_queries: int = 12) -> list[SearchQuery]:
    """queries × locations from search.yaml, capped so a run stays polite."""
    locations = list(search.locations) or [""]
    out = [SearchQuery(term=t, location=loc, hours_old=search.freshness_hours,
                       limit=search.results_per_query)
           for t in search.queries for loc in locations]
    return out[:max_queries]


def blocked(company: str, blocklist: list[str]) -> bool:
    c = company.lower()
    return any(b.lower() in c for b in blocklist if b.strip())


def run_search(connector: Connector, search: SearchConfig, seen: set[str] | None = None
               ) -> tuple[list[Posting], dict[str, str]]:
    """Run every query; return new, unblocked, de-duplicated postings and a map
    of query → error for sources that failed (never raises for one bad query)."""
    seen = seen if seen is not None else set()
    postings: list[Posting] = []
    errors: dict[str, str] = {}
    keys_this_run: set[str] = set()
    for q in build_queries(search):
        try:
            found = connector.search(q)
        except ConnectorError as exc:
            errors[f"{q.term} @ {q.location or 'anywhere'}"] = str(exc)
            continue
        for p in found:
            key = link_key(p.url) or f"title:{p.company.lower()}|{p.title.lower()}"
            if key in seen or key in keys_this_run or blocked(p.company, search.blocklist):
                continue
            keys_this_run.add(key)
            postings.append(p)
    return postings, errors


__all__ = ["Connector", "ConnectorError", "Posting", "SearchQuery", "REGISTRY",
           "blocked", "build_queries", "create", "register", "run_search"]
