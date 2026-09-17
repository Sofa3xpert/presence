"""The stories bank in the app: drafted by a proper CV read, kept or edited by the person."""

from presence.app import cvextract, cvs, server, stories
from presence.app.config_io import write_yaml

FAKE = {
    "basics": {"name": "Ada Example", "email": "", "phone": "", "location": "", "headline": "",
               "summary": "", "links": []},
    "skills": {"skills": [], "languages": []},
    "work": {"work": [{"company": "Acme", "position": "Analyst", "start": "2024", "end": "2025",
                       "highlights": ["Cut monthly reporting time by 40%", "Short"]}]},
    "education": {"education": []},
    "projects": {"projects": [{"name": "Dashboard", "description": "Sales dashboard for ops",
                               "url": "", "highlights": ["Used by 30 people every week"]}]},
}


def fake_ask(kind, model, system, prompt, schema, base_url=None, api_key=""):
    for name, sch, _ in cvextract.SECTIONS:
        if sch is schema:
            return FAKE[name]
    raise AssertionError("unknown schema")


def _client(tmp_path):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def test_a_proper_read_drafts_stories_for_the_person_to_keep(tmp_path, monkeypatch):
    c = _client(tmp_path)
    write_yaml(tmp_path / "presence.yaml", {
        "providers": {"local": {"kind": "openai-compatible",
                                "base_url": "http://localhost:11434/v1"}},
        "agents": {"scout": {"provider": "local", "model": "qwen3.5:9b"}},
    })
    m = cvs.add_cv(tmp_path, text="Ada Example\nAnalyst at Acme 2024-2025", label="Analyst")
    monkeypatch.setattr(cvextract, "ask_json", fake_ask)
    monkeypatch.setattr(server.threading, "Thread",
                        lambda target, daemon: type("T", (), {"start": lambda self: target()})())
    st = c.post(f"/cv/{m['id']}/read?json=1").get_json()
    assert st["finished"] and st["error"] is None and st["stories_added"] == 3
    bank = stories.list_stories(tmp_path)
    assert [s["text"] for s in bank] == ["Cut monthly reporting time by 40%",
                                         "Sales dashboard for ops", "Used by 30 people every week"]
    assert all(not s["confirmed"] and s["source"] == f"cv:{m['id']}" for s in bank)
    assert bank[0]["tags"] == ["Analyst · Acme"] and bank[2]["tags"] == ["Dashboard"]
    assert stories.list_stories(tmp_path, confirmed_only=True) == []  # nothing used yet
    page = c.get("/").data.decode()
    assert "Your stories" in page and "3 drafted from your CV" in page
    assert page.count("from your CV — keep?") == 3 and "kept</span>" not in page
    assert page.index("Used by 30 people") < page.index("Cut monthly reporting")  # newest first
    st = c.post(f"/cv/{m['id']}/read?json=1").get_json()
    assert st["stories_added"] == 0 and len(stories.list_stories(tmp_path)) == 3


def test_stories_routes_add_keep_edit_remove(tmp_path):
    c = _client(tmp_path)
    page = c.get("/").data.decode()
    assert "Your stories" in page and "No stories yet" in page
    r = c.post("/stories/add", data={"text": "  Rebuilt the monthly reporting pack; cut "
                                             "turnaround by 40%.  ",
                                     "tags": "reporting, Excel, "}, follow_redirects=True)
    assert b"story added" in r.data
    s = stories.list_stories(tmp_path)[0]
    assert s["confirmed"] and s["source"] == "session" and s["tags"] == ["reporting", "Excel"]
    assert s["text"] == "Rebuilt the monthly reporting pack; cut turnaround by 40%."
    page = c.get("/").data.decode()
    assert "kept</span>" in page and ">Excel</span>" in page and "Keep</button>" not in page
    r = c.post("/stories/add", data={"text": "   "}, follow_redirects=True)
    assert b"write the story first" in r.data and len(stories.list_stories(tmp_path)) == 1
    d = stories.add(tmp_path, "Wrote the SQL models finance uses.", source="cv:x",
                    confirmed=False)
    page = c.get("/").data.decode()
    assert "from your CV — keep?" in page and "Keep</button>" in page
    r = c.post(f"/stories/{d['id']}/confirm", follow_redirects=True)
    assert b"kept" in r.data and stories.get(tmp_path, d["id"])["confirmed"]
    e = stories.add(tmp_path, "Ran the weekly stand-up for a team of six.", source="cv:x",
                    confirmed=False)
    r = c.post(f"/stories/{e['id']}/update",
               data={"text": "Ran the weekly stand-up for a team of six for a year.",
                     "tags": "teamwork"}, follow_redirects=True)
    assert b"story updated and kept" in r.data
    e2 = stories.get(tmp_path, e["id"])
    assert e2["text"].endswith("for a year.") and e2["tags"] == ["teamwork"] and e2["confirmed"]
    r = c.post(f"/stories/{s['id']}/remove", follow_redirects=True)
    assert b"story removed" in r.data and stories.get(tmp_path, s["id"]) is None
    for path in ("/stories/sto-nope/remove", "/stories/sto-nope/confirm",
                 "/stories/sto-nope/update"):
        r = c.post(path, data={"text": "x"}, follow_redirects=True)
        assert b"not in your bank" in r.data
    assert len(stories.list_stories(tmp_path)) == 2
