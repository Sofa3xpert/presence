"""Connector layer tests — no network; the LinkedIn scraper is monkeypatched."""

from datetime import date

import pytest

from presence.connectors import (
    REGISTRY,
    ConnectorError,
    Posting,
    SearchQuery,
    SeenPostings,
    build_queries,
    create,
    run_search,
)
from presence.connectors.linkedin import LinkedInConnector, _rows_to_postings
from presence.core.config import SearchConfig


class FakeConnector:
    name = "fake"

    def __init__(self, results, fail_on=None):
        self.results, self.fail_on, self.queries = results, fail_on, []

    def search(self, q: SearchQuery):
        self.queries.append(q)
        if q.term == self.fail_on:
            raise ConnectorError("boom")
        return self.results.get(q.term, [])


def P(company, title, url="", **kw):
    return Posting(company=company, title=title, url=url, source="fake", **kw)


def test_registry_knows_linkedin_and_rejects_unknown():
    assert "linkedin" in REGISTRY
    assert isinstance(create("linkedin"), LinkedInConnector)
    with pytest.raises(ConnectorError, match="unknown connector"):
        create("carrier-pigeon")


def test_build_queries_is_the_cross_product_with_config_knobs():
    cfg = SearchConfig(queries=["ml engineer", "data scientist"], locations=["London", "Remote"],
                       freshness_hours=48, results_per_query=7)
    qs = build_queries(cfg)
    assert [(q.term, q.location) for q in qs] == [
        ("ml engineer", "London"), ("ml engineer", "Remote"),
        ("data scientist", "London"), ("data scientist", "Remote")]
    assert qs[0].hours_old == 48 and qs[0].limit == 7
    assert len(build_queries(cfg, max_queries=3)) == 3
    assert build_queries(SearchConfig(queries=["x"], locations=[]))[0].location == ""


def test_run_search_filters_seen_blocked_and_duplicates_and_isolates_errors():
    li = "https://www.linkedin.com/jobs/view/111"
    fake = FakeConnector({
        "ml": [P("Acme", "ML Engineer", li), P("Acme", "ML Engineer", li + "?trk=x"),
               P("SpamAgency Ltd", "ML Engineer", "https://x/2"), P("Seen Co", "ML", "https://x/3")],
        "ds": [P("Beta", "Data Scientist")],
    }, fail_on="broken")
    cfg = SearchConfig(queries=["ml", "broken", "ds"], locations=["London"],
                       blocklist=["spamagency"])
    postings, errors = run_search(fake, cfg, seen={"url:https://x/3"})
    assert [(p.company, p.title) for p in postings] == [
        ("Acme", "ML Engineer"), ("Beta", "Data Scientist")]
    assert list(errors) == ["broken @ London"] and "boom" in errors["broken @ London"]
    assert len(fake.queries) == 3


def test_rows_to_postings_cleans_scraper_output():
    rows = [
        {"title": " AI Engineer ", "company": "Acme", "location": "London, England",
         "job_url": "https://www.linkedin.com/jobs/view/5", "date_posted": "2026-09-11",
         "description": "  lots   of  text " * 100},
        {"title": "No company", "company": None, "job_url": "https://x"},
        {"title": "Nan date", "company": "Beta", "date_posted": "nan", "description": None},
    ]
    out = _rows_to_postings(rows, with_description=True)
    assert [p.company for p in out] == ["Acme", "Beta"]
    assert out[0].title == "AI Engineer" and out[0].posted == "2026-09-11"
    assert out[0].source == "linkedin"
    assert len(out[0].description) <= 600 and "  " not in out[0].description
    assert out[1].posted == "" and out[1].description == ""
    assert _rows_to_postings(rows, with_description=False)[0].description == ""


def test_linkedin_connector_wraps_scraper_failures(monkeypatch):
    c = LinkedInConnector()
    def boom(self, q):
        raise RuntimeError("429")

    monkeypatch.setattr(LinkedInConnector, "_scrape", boom)
    with pytest.raises(ConnectorError, match="RuntimeError: 429"):
        c.search(SearchQuery(term="x"))
    monkeypatch.setattr(LinkedInConnector, "_scrape",
                        lambda self, q: [{"title": "T", "company": "C", "job_url": "https://u"}])
    assert c.search(SearchQuery(term="x"))[0].candidate.company == "C"


def test_seen_store_persists(tmp_path):
    s = SeenPostings(tmp_path / "seen.json")
    s.mark(["li:1", "li:2"], on=date(2026, 9, 12))
    assert "li:1" in s and len(s) == 2
    again = SeenPostings(tmp_path / "seen.json")
    assert again.keys() == {"li:1", "li:2"}
    again.mark({"li:1"}, on=date(2026, 9, 13))  # first-seen date is kept
    assert again._seen["li:1"] == "2026-09-12"
