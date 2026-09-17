"""Proper CV reading on the person's model: schema dispatch, merging, and the routes."""

import json

from presence.app import cvextract, cvs, server
from presence.app.config_io import read_yaml, write_yaml
from presence.core import structured

FAKE = {
    "basics": {
        "name": "Ada Example",
        "email": "ada@example.org",
        "phone": "+44 7700 900123",
        "location": "London",
        "headline": "Data analyst",
        "summary": "",
        "links": ["https://github.com/ada", "linkedin.com/in/ada"],
    },
    "skills": {
        "skills": [
            {"name": "Analysis", "keywords": ["SQL", "Excel", "Power BI"]},
            {"name": "Skills", "keywords": ["Stakeholder management"]},
        ],
        "languages": ["English", "Spanish"],
    },
    "work": {
        "work": [
            {
                "company": "Acme",
                "position": "Analyst",
                "start": "2024",
                "end": "2025",
                "highlights": ["Cut monthly reporting time by 40%"],
            }
        ]
    },
    "education": {"education": []},
    "projects": {
        "projects": [
            {
                "name": "Dashboard",
                "description": "Sales dashboard",
                "url": "",
                "highlights": ["Used by 30 people"],
            }
        ]
    },
}


def fake_ask(kind, model, system, prompt, schema, base_url=None, api_key=""):
    for name, sch, _ in cvextract.SECTIONS:
        if sch is schema:
            return FAKE[name]
    raise AssertionError("unknown schema")


def test_extract_merges_sections_and_flattens_fields():
    out = cvextract.extract("cv text", "local", "qwen3.5:9b", ask=fake_ask)
    assert out["errors"] == {} and out["basics"]["name"] == "Ada Example"
    f = cvextract.to_fields(out)
    assert f["skills"] == ["Analysis", "SQL", "Excel", "Power BI", "Stakeholder management"]
    assert f["links"] == ["github.com/ada", "linkedin.com/in/ada"] and f["phone"].startswith("+44")
    facts = cvextract.facts(out)
    assert {x["claim"] for x in facts} == {
        "Cut monthly reporting time by 40%",
        "Sales dashboard",
        "Used by 30 people",
    }
    assert facts[0]["source"] == "Analyst · Acme"


def test_a_failing_section_is_reported_not_fatal():
    def flaky(kind, model, system, prompt, schema, base_url=None, api_key=""):
        if schema is cvextract.WORK:
            raise structured.StructuredError("the local engine did not answer")
        return fake_ask(kind, model, system, prompt, schema)

    out = cvextract.extract("cv", "local", "m", ask=flaky)
    assert "work" in out["errors"] and out["basics"]["name"] == "Ada Example"


def test_local_engine_uses_the_one_v1_transport(monkeypatch):
    from fake_openai import install, reply

    seen = install(monkeypatch, reply('```json\n{"name": "Ada"}\n```'))
    out = structured.ask_json(
        "local",
        "qwen3.5:9b",
        "sys",
        "prompt",
        {"type": "object"},
        base_url="http://127.0.0.1:57726/v1",
    )
    assert out == {"name": "Ada"}
    assert seen["base_url"] == "http://127.0.0.1:57726/v1" and seen["api_key"] == "ollama"
    call = seen["calls"][0]
    assert call["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "answer",
                                                       "schema": {"type": "object"}}}
    assert call["temperature"] == 0 and call["extra_body"]["reasoning_effort"] == "none"
    assert call["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert structured._json_from_text('text before {"a": 1} after') == {"a": 1}


def test_read_route_promotes_fields_and_fills_the_form(tmp_path, monkeypatch):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    c = app.test_client()
    write_yaml(
        tmp_path / "presence.yaml",
        {
            "providers": {
                "local": {"kind": "openai-compatible", "base_url": "http://localhost:11434/v1"}
            },
            "agents": {"scout": {"provider": "local", "model": "qwen3.5:9b"}},
        },
    )
    m = cvs.add_cv(tmp_path, text="2024 placement at acme 42\nno name line here 7",
                   label="Analyst")
    assert m["fields"]["name"] == ""  # the rough read found no name
    monkeypatch.setattr(cvextract, "ask_json", fake_ask)
    monkeypatch.setattr(
        server.threading,
        "Thread",
        lambda target, daemon: type("T", (), {"start": lambda self: target()})(),
    )
    r = c.post(f"/cv/{m['id']}/read?json=1")
    st = r.get_json()
    assert st["finished"] and st["phase"] == "done" and st["error"] is None
    meta = cvs.get(tmp_path, m["id"])
    assert meta["read_with"] == "qwen3.5:9b" and meta["fields"]["name"] == "Ada Example"
    assert (
        json.loads((tmp_path / "cvs" / m["id"] / "extracted.json").read_text())["work"]["work"][0][
            "company"
        ]
        == "Acme"
    )
    prof = read_yaml(tmp_path / "profile.yaml")
    assert prof["identity"]["name"] == "Ada Example" and "SQL" in prof["skills"]
    assert c.get(f"/cv/{m['id']}/read/status").get_json()["phase"] == "done"
    page = c.get("/").data.decode()
    assert "read with qwen3.5:9b" in page
