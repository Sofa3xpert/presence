"""Connector layer tests — no network; board responses are fed as JSON."""

from datetime import date

import pytest

from presence.connectors import (
    CATALOG,
    REGISTRY,
    ConnectorError,
    Posting,
    available,
    boards,
    create,
)
from presence.connectors.base import matches, run_sources
from presence.core.config import SearchConfig, SourceEntry, load_sources

SRC = SourceEntry(id="acme", provider="greenhouse", label="Acme", config={"board": "acme"})


def P(title, location="London, UK", url="https://x/1", posted="", company="Acme"):
    return Posting(company=company, title=title, location=location, url=url, posted=posted)


def test_registry_has_the_five_published_apis_and_no_scrapers():
    assert set(REGISTRY) == {"greenhouse", "lever", "ashby", "workable", "smartrecruiters"}
    assert {c["provider"] for c in available()} == set(REGISTRY)
    assert all(c["kind"] in ("board", "inbox", "aggregator") for c in CATALOG)
    assert not any(c["provider"] in ("linkedin", "indeed", "jobspy") for c in CATALOG)
    with pytest.raises(ConnectorError, match="unknown provider"):
        create("linkedin")


def test_filters():
    s = SearchConfig(
        locations=["London"],
        title_include=["engineer", "scientist"],
        blocklist=["spamco"],
        freshness_hours=48,
    )
    today = date(2026, 9, 14)
    assert matches(P("AI Engineer", posted="2026-09-13"), s, today)
    assert not matches(P("Senior AI Engineer", posted="2026-09-13"), s, today)
    assert not matches(P("AI Engineer", location="Paris", posted="2026-09-13"), s, today)
    assert matches(P("AI Engineer", location="Remote - Europe", posted="2026-09-13"), s, today)
    assert not matches(P("Sales Lead", posted="2026-09-13"), s, today)
    assert not matches(P("AI Engineer", posted="2026-09-01"), s, today)
    assert matches(P("AI Engineer", posted=""), s, today)  # unknown date: kept
    assert not matches(P("AI Engineer", company="SpamCo Ltd", posted="2026-09-13"), s, today)
    assert matches(P("Anything"), SearchConfig())  # defaults: anywhere, any title bar seniors


def test_run_sources_isolates_failures_and_dedupes(monkeypatch):
    class Fake:
        provider = "greenhouse"

        def fetch(self, src):
            if src.id == "broken":
                raise ConnectorError("boom")
            return [
                P("AI Engineer"),
                P("AI Engineer", url="https://x/1?utm=z"),
                P("Data Scientist", url="https://x/2"),
                P("Old", url="https://x/3"),
            ]

    sources = [
        SRC,
        SourceEntry(id="broken", provider="greenhouse", label="B", config={"board": "b"}),
        SourceEntry(
            id="off", provider="greenhouse", label="Off", enabled=False, config={"board": "o"}
        ),
    ]
    postings, errors = run_sources(
        sources, SearchConfig(), seen={"url:https://x/3"}, connectors={"greenhouse": Fake()}
    )
    assert [p.title for p in postings] == ["AI Engineer", "Data Scientist"]
    assert list(errors) == ["broken"] and "boom" in errors["broken"]


def test_board_mappers_use_published_shapes(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if "greenhouse" in url:
            return {
                "jobs": [
                    {
                        "title": "ML Engineer",
                        "location": {"name": "London"},
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                        "updated_at": "2026-09-10T10:00:00Z",
                    }
                ]
            }
        if "lever" in url:
            return [
                {
                    "text": "Data Scientist",
                    "categories": {"location": "London"},
                    "allLocations": ["Remote"],
                    "hostedUrl": "https://jobs.lever.co/acme/2",
                    "createdAt": 1789171200000,
                }
            ]
        if "ashby" in url:
            return {
                "jobs": [
                    {
                        "title": "SWE",
                        "location": "London",
                        "secondaryLocations": [],
                        "jobUrl": "https://jobs.ashbyhq.com/acme/3",
                        "publishedAt": "2026-09-11",
                    }
                ]
            }
        if "workable" in url:
            return {
                "jobs": [
                    {
                        "title": "AI Eng",
                        "city": "London",
                        "country": "UK",
                        "url": "https://apply.workable.com/acme/j/4",
                        "published_on": "2026-09-12",
                    }
                ]
            }
        return {
            "content": [
                {
                    "name": "Analyst",
                    "id": "5",
                    "location": {"city": "London", "country": "UK"},
                    "releasedDate": "2026-09-13T00:00:00Z",
                }
            ],
            "totalFound": 1,
        }

    monkeypatch.setattr(boards, "_get", fake_get)
    for provider in REGISTRY:
        src = SourceEntry(id="acme", provider=provider, label="Acme", config={"board": "acme"})
        out = create(provider).fetch(src)
        assert len(out) == 1 and out[0].company == "Acme" and out[0].source == provider
        assert out[0].url.startswith("https://") and out[0].posted.startswith("2026-09-1")
    assert len(calls) == 5
    with pytest.raises(ConnectorError, match="no config.board"):
        create("greenhouse").fetch(SourceEntry(id="x", provider="greenhouse", label="X"))


def test_sources_yaml_loads(tmp_path):
    (tmp_path / "sources.yaml").write_text(
        "sources:\n  - {id: figma, provider: greenhouse, label: Figma, config: {board: figma}}\n"
        "  - {id: openai, provider: ashby, label: OpenAI, enabled: false, "
        "config: {board: openai}}\n"
    )
    srcs = load_sources(tmp_path)
    assert [s.id for s in srcs] == ["figma", "openai"] and srcs[1].enabled is False
