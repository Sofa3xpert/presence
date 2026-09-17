"""Job descriptions, company notes and the stories bank on disk; budget on structured calls."""

import pytest

from presence.app import company, jd, stories
from presence.core import structured
from presence.core.budget import BudgetExhausted, DailyBudget


def test_jd_round_trip(tmp_path):
    assert jd.load(tmp_path, 7) == "" and not jd.has(tmp_path, 7)
    jd.save(tmp_path, 7, "  Graduate Analyst\n\nWe need someone who owns the monthly numbers.  ")
    assert jd.has(tmp_path, 7) and jd.load(tmp_path, 7).startswith("Graduate Analyst")
    assert jd.path(tmp_path, 7) == tmp_path / "jd" / "7.txt"


def test_company_notes(tmp_path):
    assert company.list_notes(tmp_path, 3) == [] and company.text(tmp_path, 3) == ""
    a = company.add(tmp_path, 3, "They just launched a mobile app.", title="Product news")
    b = company.add(tmp_path, 3, "The ad says the team is four people.")
    assert [n["n"] for n in company.list_notes(tmp_path, 3)] == [1, 2]
    assert "Product news\nThey just launched" in company.text(tmp_path, 3)
    assert company.remove(tmp_path, 3, a["n"]) and not company.remove(tmp_path, 3, 99)
    assert company.list_notes(tmp_path, 3)[0]["n"] == b["n"]
    with pytest.raises(ValueError):
        company.add(tmp_path, 3, "   ")


def test_stories_bank(tmp_path):
    s = stories.add(
        tmp_path,
        "Rebuilt the monthly reporting pack; cut turnaround by 40%.",
        tags=["reporting"],
        job_id=5,
    )
    assert s["confirmed"] and s["source"] == "session" and s["id"].startswith("sto-")
    same = stories.add(tmp_path, "rebuilt the monthly   reporting pack; cut turnaround by 40%.")
    assert same["id"] == s["id"]  # never duplicated, whatever the spacing or case
    d = stories.add(
        tmp_path, "Wrote the SQL models the finance team uses.", source="cv:x", confirmed=False
    )
    assert [x["id"] for x in stories.list_stories(tmp_path, confirmed_only=True)] == [s["id"]]
    assert stories.confirm(tmp_path, d["id"]) and len(stories.list_stories(tmp_path, True)) == 2
    assert stories.update(
        tmp_path, d["id"], text="Wrote the SQL models finance still uses daily.", tags=["sql"]
    )
    assert stories.get(tmp_path, d["id"])["tags"] == ["sql"]
    assert stories.remove(tmp_path, s["id"]) and not stories.remove(tmp_path, "sto-nope")
    with pytest.raises(ValueError):
        stories.add(tmp_path, " ")


def test_seed_from_cv_drafts_unconfirmed(tmp_path):
    facts = [
        {"claim": "Cut monthly reporting time by 40%", "source": "Analyst · Acme"},
        {"claim": "Short", "source": "x"},
        {"claim": "Used by 30 people", "source": "Dashboard"},
    ]
    assert stories.seed_from_cv(tmp_path, "cv-1", facts) == 2
    assert all(
        not s["confirmed"] and s["source"] == "cv:cv-1" for s in stories.list_stories(tmp_path)
    )
    assert stories.seed_from_cv(tmp_path, "cv-1", facts) == 0  # a second read adds nothing


def test_ask_json_charges_the_budget_and_stops_at_the_cap(tmp_path, monkeypatch):
    def fake(kind, model, system, prompt, schema, *, base_url=None, api_key=""):
        structured.LAST_USAGE.update(input_tokens=30, output_tokens=20)
        return {"ok": True}

    monkeypatch.setattr(structured, "_dispatch", fake)
    b = DailyBudget(80, tmp_path / "budget.json")
    assert structured.ask_json("local", "m", "s", "p", {}, budget=b) == {"ok": True}
    assert b.spent_today == 50
    structured.ask_json("local", "m", "s", "p", {}, budget=b)
    with pytest.raises(BudgetExhausted):
        structured.ask_json("local", "m", "s", "p", {}, budget=b)
    assert structured.ask_json("local", "m", "s", "p", {}) == {"ok": True}  # no budget: no gate
