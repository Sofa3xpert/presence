"""The message session: questions, angles, compose and its checks, storage, and the routes.
No network — the model is a fake keyed on schema identity, the background thread runs inline."""

import re
from datetime import date

import pytest

from presence.agents import session
from presence.app import jd, server, sessions, stories
from presence.app.config_io import write_yaml
from presence.core.budget import BudgetExhausted
from presence.tracker import Candidate, Tracker

TURNS = [
    {"question": "Why this one, honestly?", "why": "the honest reason is the best opener",
     "angle": "", "angle_from": "", "enough": False},
    {"question": "What did you notice about them?", "why": "something the CV cannot say",
     "angle": "You rebuilt a reporting pack — that meets their 'own the monthly numbers' line",
     "angle_from": "answer:1", "enough": False},
    {"question": "", "why": "", "angle": "", "angle_from": "", "enough": True},
]
MESSAGE_OK = (
    "Hello,\n"
    "I rebuilt the monthly reporting pack at my last place, which is the 'own the monthly "
    "numbers' part of the Analyst role at Acme. I read your product note on the new dashboard "
    "and it is the kind of thing I would want to test in month one.\n"
    "Best,\n"
    "Ada"
)


def _attribute(prompt):
    """A fake attribution: greeting, sign-off and name → profile; the rest by content."""
    out = []
    for line in prompt.splitlines():
        m = re.match(r"^(\d+)\. (.*)$", line)
        if not m:
            continue
        n, text = int(m.group(1)), m.group(2)
        if text in ("Hello,", "Best,", "Ada"):
            src = "profile"
        elif "rebuilt" in text:
            src = "angle:1"
        elif "product note" in text:
            src = "Answer: 2"
        elif "Mandarin" in text:
            src = ""
        elif "Something rejected" in text:
            src = "angle:2"
        else:
            src = "answer:1"
        out.append({"n": n, "source": src})
    return {"sentences": out}


def _ctx(**kw):
    base = dict(
        company="Acme", title="Analyst",
        description="We need someone to own the monthly numbers.",
        transcript=[
            {"n": 1, "question": "Why this one?", "why": "",
             "answer": "I rebuilt the monthly reporting pack at my last place.", "skipped": False},
            {"n": 2, "question": "Noticed?", "why": "",
             "answer": "I read your product note on the new dashboard.", "skipped": False},
            {"n": 3, "question": "First month?", "why": "", "answer": "", "skipped": True},
        ],
        angles=[
            {"n": 1, "text": "Reporting pack meets 'own the monthly numbers'", "from": "answer:1",
             "verdict": "accepted", "final": "Reporting pack meets 'own the monthly numbers'"},
            {"n": 2, "text": "Something rejected", "from": "answer:2", "verdict": "rejected",
             "final": ""},
        ],
        stories=[{"id": "sto-abc123", "text": "We shipped the dashboard as a team of three.",
                  "confirmed": True}],
        profile={"name": "Ada", "links": ["github.com/ada"]},
    )
    base.update(kw)
    return session.Context(**base)


# ---------------------------------------------------------------- the turn

def test_next_turn_keeps_only_supported_angles_and_stops_when_enough():
    calls = []

    def ask(system, prompt, schema):
        assert schema is session.TURN and "one question at a time" in system
        calls.append(prompt)
        return TURNS[len(calls) - 1]

    ctx = session.Context(company="Acme", title="Analyst", description="Own the numbers.")
    t1 = session.next_turn(ctx, ask)
    assert t1["question"] == "Why this one, honestly?" and t1["angle"] is None
    assert not t1["enough"] and t1["why"] == "the honest reason is the best opener"
    assert "Acme" in calls[0] and "first question" in calls[0] and "Own the numbers." in calls[0]
    ctx.transcript.append({"n": 1, "question": t1["question"], "why": t1["why"],
                           "answer": "I rebuilt the reporting pack.", "skipped": False})
    t2 = session.next_turn(ctx, ask)
    assert t2["angle"] == {"text": TURNS[1]["angle"], "from": "answer:1"}
    assert "answer:1" in calls[1] and "I rebuilt the reporting pack." in calls[1]
    ctx.transcript.append({"n": 2, "question": t2["question"], "why": "",
                           "answer": "Their ad reads like a person wrote it.", "skipped": False})
    t3 = session.next_turn(ctx, ask)
    assert t3["enough"] and t3["question"] == ""
    # an angle that names a source which does not exist is dropped; "enough" needs an answer;
    # an empty question is filled from the pool so the flow never stalls
    fresh = session.Context(company="Acme", title="Analyst")
    out = session.next_turn(fresh, lambda s, p, sch: {
        "question": "", "why": "", "angle": "x", "angle_from": "answer:9", "enough": True})
    assert out["angle"] is None and out["enough"] is False
    assert out["question"] == session.QUESTIONS[0]
    # "ask me more" overrides the model's enough
    ctx.wants_more = True
    out = session.next_turn(ctx, lambda s, p, sch: TURNS[2])
    assert not out["enough"] and out["question"]


# ---------------------------------------------------------------- compose and checks

def test_compose_cites_sources_and_the_checks_remove_unsourced_sentences():
    ctx = _ctx()
    prompts = []
    msg = MESSAGE_OK.replace("\nBest,", " I also speak fluent Mandarin. Something rejected said "
                                        "this. I was passionate about it!\nBest,")

    def ask(system, prompt, schema):
        prompts.append(prompt)
        if schema is session.DRAFT:
            return {"message": msg}
        if schema is session.ATTRIBUTION:
            return _attribute(prompt)
        raise AssertionError("unknown schema")

    out = session.compose(ctx, "email", "plain", ask)
    p = prompts[0]  # the compose prompt
    assert "angle:1 — Reporting pack" in p and "story:sto-abc123 — We shipped" in p
    assert "profile — name: Ada; links: github.com/ada" in p and "90 to 140 words" in p
    assert "Something rejected" not in p  # a rejected angle never reaches the model
    assert "answer:3" not in p  # a skipped question has no answer to cite
    assert "Hello,\nI applied for the Junior Analyst role" in p  # the worked example
    assert session.split_sentences("A one; b two. C three!\nBest,\nAda") == \
        ["A one;", "b two.", "C three!", "Best,", "Ada"]
    assert out["draft"].startswith("Hello,\nI rebuilt")
    assert out["sources"][2]["source"] == "answer:2"  # "Answer: 2" normalised
    # compose hands back cleaned text: disallowed sentences are already gone, and it says so
    assert "Mandarin" not in out["draft"] and "Something rejected" not in out["draft"]
    assert any("removed a sentence with no source" in i and "Mandarin" in i
               for i in out["issues"])
    assert any("not allowed (angle:2)" in i for i in out["issues"])
    draft, issues = session.apply_checks(out, ctx, "email")
    assert "\nBest,\nAda" in draft and "passionate" in draft  # flagged, not silently cut
    assert "banned phrase: “passionate”" in issues and "no exclamation marks" in issues
    assert any(i.startswith("too short for an email") for i in issues)
    assert session.check(out, ctx, "email") == issues
    assert sum("Rewrite this message" in q for q in prompts) == 1  # one repair pass, no more
    with pytest.raises(ValueError):
        session.compose(ctx, "fax", "plain", ask)


def test_checks_cover_length_per_channel_names_and_we():
    ctx = _ctx()
    long = {"sources": [{"sentence": "Acme Analyst " + "word " * 150, "source": "answer:1"}]}
    assert any("too long for a LinkedIn message" in i
               for i in session.check(long, ctx, "linkedin_message"))
    assert any("too long for a connection note" in i
               for i in session.check(long, ctx, "connection_note"))
    assert any("too long for an email" in i for i in session.check(long, ctx, "email"))
    short = {"sources": [{"sentence": "I shipped the dashboard alone.",
                          "source": "story:sto-abc123"}]}
    issues = session.check(short, ctx, "connection_note")
    assert any("company name is missing" in i and "Acme" in i for i in issues)
    assert any("role title is missing" in i and "Analyst" in i for i in issues)
    assert any("group work stays “we”" in i for i in issues)
    ok = {"sources": [{"sentence": "We shipped the dashboard as a team of three, which is why "
                                   "the Analyst role at Acme fits.", "source": "story:sto-abc123"}]}
    assert session.check(ok, ctx, "connection_note") == []
    # a bare draft with no sources is one unsourced sentence: nothing is kept
    draft, issues = session.apply_checks({"draft": "Hire me."}, ctx, "email")
    assert draft == "" and any("nothing left to say" in i for i in issues)
    # the why panel labels are plain words
    labels = session.labels(ctx)
    assert labels["answer:1"].startswith("your answer 1: “I rebuilt")
    assert labels["angle:1"].startswith("angle 1 you kept") and "angle:2" not in labels
    assert labels["profile"] == "your confirmed profile" and labels["description"]


def test_model_errors_become_plain_words():
    assert session.plain_error(BudgetExhausted(200_000, 200_000)) == session.BUDGET_MESSAGE
    assert "used up" in session.BUDGET_MESSAGE and "settings" in session.BUDGET_MESSAGE
    assert session.plain_error(RuntimeError("boom")) == "RuntimeError: boom"


def test_asker_binds_the_model_and_the_budget(monkeypatch):
    seen = {}

    def fake(kind, model, system, prompt, schema, base_url=None, api_key="", budget=None):
        seen.update(kind=kind, model=model, base_url=base_url, budget=budget)
        return {}

    monkeypatch.setattr(session, "ask_json", fake)
    ask = session.asker({"kind": "local", "model": "m", "base_url": "http://127.0.0.1:1/v1",
                         "api_key": ""}, budget="B")
    ask("s", "p", session.TURN)
    assert seen == {"kind": "local", "model": "m", "base_url": "http://127.0.0.1:1/v1",
                    "budget": "B"}


# ---------------------------------------------------------------- storage

def test_sessions_storage(tmp_path):
    s = sessions.current(tmp_path, 4, today=date(2026, 9, 16))
    assert s["transcript"] == [] and s["date"] == "2026-09-16"
    assert sessions.list_for(tmp_path, 4) == []
    q = sessions.add_question(s, "Why this one?", "the honest reason")
    assert sessions.pending(s) is q and sessions.answer(s, " ") is None
    assert sessions.answer(s, "Because of the product.")["n"] == 1
    assert sessions.pending(s) is None
    sessions.add_question(s, "Noticed?", "")
    assert sessions.skip(s)["skipped"] is True and sessions.skip(s) is None
    a = sessions.add_angle(s, "The product angle", "answer:1")
    assert sessions.set_verdict(s, a["n"], "edit", "My wording")["final"] == "My wording"
    assert sessions.set_verdict(s, a["n"], "edit", "  ")["verdict"] == "accepted"
    assert sessions.set_verdict(s, a["n"], "reject")["verdict"] == "rejected"
    assert sessions.set_verdict(s, 99, "accept") is None
    assert sessions.set_verdict(s, 1, "maybe") is None
    sessions.apply_turn(s, {"question": "", "why": "", "angle": None, "enough": True})
    assert s["stopped"] and sessions.pending(s) is None
    sessions.apply_turn(s, {"question": "One more?", "why": "w",
                            "angle": {"text": "t", "from": "answer:1"}, "enough": False})
    assert not s["stopped"] and sessions.pending(s)["question"] == "One more?"
    assert s["angles"][1]["n"] == 2 and s["angles"][1]["verdict"] == ""
    assert sessions.story_verdict(s, "sto-1", "use") and sessions.story_verdict(s, "sto-1", "skip")
    assert s["stories"] == {"use": [], "skip": ["sto-1"]}
    assert not sessions.story_verdict(s, "sto-1", "maybe")
    sessions.add_draft(s, "email", "warmer", {"draft": "Hello", "sources": [], "issues": ["x"]})
    p = sessions.save(tmp_path, 4, s)
    assert p == tmp_path / "sessions" / "4" / "2026-09-16.json"
    again = sessions.current(tmp_path, 4, today=date(2026, 9, 16))
    assert again["tone"] == "warmer" and again["drafts"][0]["issues"] == ["x"]
    assert sessions.text_of(s, answer_n="1") == "Because of the product."
    assert sessions.text_of(s, angle_n="1") == "" and sessions.text_of(s, angle_n="2") == "t"
    # the next day: a still-open session is picked up; a finished one is not
    later = sessions.current(tmp_path, 4, today=date(2026, 9, 17))
    assert later["date"] == "2026-09-16"  # open: a question is waiting
    later["stopped"] = True
    sessions.save(tmp_path, 4, later)
    assert sessions.current(tmp_path, 4, today=date(2026, 9, 17))["date"] == "2026-09-17"
    assert [x["date"] for x in sessions.list_for(tmp_path, 4)] == ["2026-09-16"]
    assert sessions.load(tmp_path, 4, "2026-01-01") is None


# ---------------------------------------------------------------- the routes

@pytest.fixture
def app(tmp_path, monkeypatch):
    a = server.create_app(tmp_path)
    a.config["TESTING"] = True
    write_yaml(tmp_path / "presence.yaml", {
        "providers": {"local": {"kind": "openai-compatible",
                                "base_url": "http://localhost:11434/v1"}},
        "agents": {"scout": {"provider": "local", "model": "qwen3.5:9b"}},
    })
    write_yaml(tmp_path / "profile.yaml", {"identity": {"name": "Ada", "email": ""},
                                           "links": ["github.com/ada"], "confirmed": True})
    t = Tracker(tmp_path / "tracker.db")
    job, _ = t.ingest(Candidate(company="Acme", title="Analyst", url="https://x/1",
                                source="greenhouse"))
    t.close()
    monkeypatch.setattr(
        server.threading, "Thread",
        lambda target, daemon: type("T", (), {"start": lambda self: target()})(),
    )
    return a.test_client(), tmp_path, job.id


def _fake(turns, draft=None):
    turns = list(turns)

    def fake_ask(kind, model, system, prompt, schema, base_url=None, api_key="", budget=None):
        assert kind == "local" and model == "qwen3.5:9b" and budget is not None
        if schema is session.TURN:
            return turns.pop(0)
        if schema is session.DRAFT:
            return {"message": draft}
        if schema is session.ATTRIBUTION:
            return _attribute(prompt)
        raise AssertionError("unknown schema")

    return fake_ask


def test_page_paste_box_description_and_company_notes(app):
    c, data, jid = app
    page = c.get(f"/tracker/{jid}/session").data.decode()
    assert "Acme" in page and "Analyst" in page and "Paste the job description here" in page
    assert "Nothing here is sent. Presence asks, you answer, it writes from your words." in page
    assert 'class="where local"' in page and "Add your CV in Setup" in page
    r = c.post(f"/tracker/{jid}/jd", data={"text": "Own the monthly numbers."},
               follow_redirects=True)
    assert b"description saved" in r.data and jd.load(data, jid) == "Own the monthly numbers."
    assert "Own the monthly numbers." in c.get(f"/tracker/{jid}/session").data.decode()
    assert b"paste the description first" in c.post(f"/tracker/{jid}/jd", data={"text": " "},
                                                    follow_redirects=True).data
    r = c.post(f"/tracker/{jid}/company", data={"text": "They just launched a mobile app.",
                                                "title": "News"}, follow_redirects=True)
    assert b"kept with this job" in r.data and b"mobile app" in r.data
    assert b"paste something first" in c.post(f"/tracker/{jid}/company", data={"text": ""},
                                              follow_redirects=True).data
    r = c.post(f"/tracker/{jid}/company/1/remove", follow_redirects=True)
    assert b"note removed" in r.data and b"mobile app" not in r.data
    r = c.get("/tracker/999/session", follow_redirects=True)
    assert r.status_code == 200 and b"not in your tracker" in r.data
    page = c.get("/tracker").data.decode()
    assert f"/tracker/{jid}/session" in page and "Prepare a message" in page
    assert 'value="messaged"' in page


def test_cloud_marker_names_the_service(app):
    c, data, jid = app
    write_yaml(data / "presence.yaml", {
        "providers": {"anthropic": {"kind": "anthropic", "api_key_secret": "ANTHROPIC_API_KEY"}},
        "agents": {"scout": {"provider": "anthropic", "model": "claude-x"}},
    })
    page = c.get(f"/tracker/{jid}/session").data.decode()
    assert 'class="where cloud"' in page and "through a service — Anthropic" in page


def test_answer_skip_enough_more_and_angle_verdicts(app, monkeypatch):
    c, data, jid = app
    monkeypatch.setattr(session, "ask_json", _fake(TURNS + [TURNS[0]]))
    st = c.post(f"/tracker/{jid}/session/ask?json=1").get_json()
    assert st["finished"] and st["phase"] == "done" and st["error"] is None
    page = c.get(f"/tracker/{jid}/session").data.decode()
    assert "Why this one, honestly?" in page and "the honest reason" in page
    assert "That's enough" in page and "Nothing here is sent" not in page
    assert (c.post(f"/tracker/{jid}/session/ask?json=1").get_json()["error"]
            == "answer or skip the question first")
    r = c.post(f"/tracker/{jid}/session/answer", data={"answer": " "}, follow_redirects=True)
    assert b"type an answer, or skip" in r.data
    r = c.post(f"/tracker/{jid}/session/answer",
               data={"answer": "I rebuilt the reporting pack."}, follow_redirects=True)
    sess = sessions.current(data, jid)
    assert sess["transcript"][0]["answer"] == "I rebuilt the reporting pack."
    assert sess["transcript"][1]["question"] == "What did you notice about them?"  # followed
    assert sess["angles"][0]["from"] == "answer:1" and sess["angles"][0]["verdict"] == ""
    page = r.data.decode()
    assert "meets their" in page and "Not this" in page and "your answer 1" in page
    r = c.post(f"/tracker/{jid}/session/angle/1", data={"verdict": "edit", "text": "My words"},
               follow_redirects=True)
    assert b"in your words" in r.data and b"My words" in r.data
    assert sessions.current(data, jid)["angles"][0]["final"] == "My words"
    c.post(f"/tracker/{jid}/session/angle/1", data={"verdict": "reject"})
    assert sessions.current(data, jid)["angles"][0]["verdict"] == "rejected"
    r = c.post(f"/tracker/{jid}/session/angle/7", data={"verdict": "accept"},
               follow_redirects=True)
    assert b"not in this session" in r.data
    # skip the second question; the model then says it has enough
    c.post(f"/tracker/{jid}/session/answer", data={"skip": "1"})
    sess = sessions.current(data, jid)
    assert sess["transcript"][1]["skipped"] and sess["stopped"] and len(sess["transcript"]) == 2
    page = c.get(f"/tracker/{jid}/session").data.decode()
    assert "has enough for a message" in page and "Ask me more" in page
    r = c.post(f"/tracker/{jid}/session/more", follow_redirects=True)
    sess = sessions.current(data, jid)
    assert not sess["stopped"] and len(sess["transcript"]) == 3
    assert b"Why this one, honestly?" in r.data
    r = c.post(f"/tracker/{jid}/session/enough", follow_redirects=True)
    assert b"press Write" in r.data and sessions.current(data, jid)["stopped"]
    r = c.post(f"/tracker/{jid}/session/answer", data={"answer": "Done."}, follow_redirects=True)
    assert b"noted" in r.data and sessions.current(data, jid)["transcript"][2]["answer"] == "Done."
    assert r.data.count(b"Save this to my stories") == 2  # on each answer given
    assert b"there is no question waiting" in c.post(f"/tracker/{jid}/session/answer",
                                                     data={"answer": "x"},
                                                     follow_redirects=True).data


def test_draft_route_writes_from_the_persons_words_with_a_why_panel(app, monkeypatch):
    c, data, jid = app
    jd.save(data, jid, "We need someone to own the monthly numbers.")
    monkeypatch.setattr(session, "ask_json", _fake(TURNS, MESSAGE_OK))
    st = c.post(f"/tracker/{jid}/session/draft?json=1",
                data={"channel": "email", "tone": "plain"}).get_json()
    assert "answer a question or two first" in st["error"]  # nothing of theirs to write from
    c.post(f"/tracker/{jid}/session/ask")
    c.post(f"/tracker/{jid}/session/answer",
           data={"answer": "I rebuilt the monthly reporting pack at my last place."})
    c.post(f"/tracker/{jid}/session/angle/1", data={"verdict": "accept"})
    c.post(f"/tracker/{jid}/session/answer",
           data={"answer": "I read your product note on the new dashboard."})
    st = c.post(f"/tracker/{jid}/session/draft?json=1",
                data={"channel": "email", "tone": "warmer"}).get_json()
    assert st["finished"] and st["error"] is None and st["what"] == "draft"
    assert c.get(f"/tracker/{jid}/session/status").get_json()["phase"] == "done"
    sess = sessions.current(data, jid)
    d = sess["drafts"][-1]
    assert d["channel"] == "email" and d["tone"] == "warmer" and sess["tone"] == "warmer"
    assert d["draft"].startswith("Hello,\nI rebuilt") and d["draft"].endswith("Best,\nAda")
    assert [s["source"] for s in d["sources"]] == ["profile", "angle:1", "answer:2", "profile",
                                                   "profile"]
    page = c.get(f"/tracker/{jid}/session").data.decode()
    assert "Why each line" in page and "angle 1 you kept" in page and "your answer 2" in page
    assert 'id="copy"' in page and "Mark as sent" in page and "Presence never sends" in page
    assert "too short for an email" in page  # the check is shown, never hidden
    r = c.post(f"/tracker/{jid}/session/draft", data={"channel": "fax", "tone": "plain"},
               follow_redirects=True)
    assert b"pick a channel and a tone" in r.data
    r = c.post(f"/tracker/{jid}/session/draft", data={"channel": "connection_note",
                                                      "tone": "plain"}, follow_redirects=True)
    assert b"writing with qwen3.5:9b" in r.data
    assert len(sessions.current(data, jid)["drafts"]) == 2


def test_sent_records_messaged_and_story_saves_to_the_bank(app, monkeypatch):
    c, data, jid = app
    monkeypatch.setattr(session, "ask_json", _fake(TURNS))
    c.post(f"/tracker/{jid}/session/ask")
    c.post(f"/tracker/{jid}/session/answer",
           data={"answer": "I rebuilt the monthly reporting pack."})
    r = c.post(f"/tracker/{jid}/session/sent", follow_redirects=True)
    assert b"recorded" in r.data and b"nothing was sent by Presence" in r.data
    c.post(f"/tracker/{jid}/event", data={"kind": "messaged"})  # the tracker row's own select
    t = Tracker(data / "tracker.db")
    assert t.get(jid).status == "to_apply"  # the status stays
    assert [k for k, _, _ in t.events(jid)] == ["messaged", "messaged"]
    assert t.timeline(jid).startswith("messaged ")
    t.close()
    assert "messaged" in c.get(f"/tracker/{jid}/session").data.decode()
    r = c.post(f"/tracker/{jid}/session/story", data={"answer": "1"}, follow_redirects=True)
    assert b"saved to your stories" in r.data
    bank = stories.list_stories(data, confirmed_only=True)
    assert len(bank) == 1 and bank[0]["text"] == "I rebuilt the monthly reporting pack."
    assert bank[0]["job_id"] == jid and bank[0]["source"] == "session" and bank[0]["confirmed"]
    r = c.post(f"/tracker/{jid}/session/story", data={"angle": "1"}, follow_redirects=True)
    assert b"saved to your stories" in r.data and len(stories.list_stories(data)) == 2
    page = c.get(f"/tracker/{jid}/session").data.decode()
    assert "Your stories" in page and bank[0]["text"] in page and page.count(">Skip<") >= 2
    c.post(f"/tracker/{jid}/session/stories", data={"id": bank[0]["id"], "verdict": "skip"})
    assert sessions.current(data, jid)["stories"]["skip"] == [bank[0]["id"]]
    assert 'class="story skip"' in c.get(f"/tracker/{jid}/session").data.decode()
    r = c.post(f"/tracker/{jid}/session/story", data={"answer": "9"}, follow_redirects=True)
    assert b"nothing to save there" in r.data


def test_page_shows_a_live_status_while_the_model_thinks(app, monkeypatch):
    c, data, jid = app
    monkeypatch.setattr(session, "ask_json", _fake(TURNS))
    monkeypatch.setattr(  # a thread that never gets to run: the call stays in flight
        server.threading, "Thread",
        lambda target, daemon: type("T", (), {"start": lambda self: None})(),
    )
    r = c.post(f"/tracker/{jid}/session/ask", follow_redirects=True)
    page = r.data.decode()
    assert "thinking of a question with qwen3.5:9b" in page and 'id="turn-status"' in page
    assert "Answer</button>" not in page and "Ask me a question" not in page
    st = c.get(f"/tracker/{jid}/session/status").get_json()
    assert not st["finished"] and st["phase"] == "asking" and st["what"] == "turn"
    # a second press while it runs does not start another call
    assert c.post(f"/tracker/{jid}/session/ask?json=1").get_json() == st


def test_budget_exhausted_and_no_model_are_plain_flashes(app, monkeypatch):
    c, data, jid = app

    def broke(kind, model, system, prompt, schema, base_url=None, api_key="", budget=None):
        raise BudgetExhausted(200_000, 200_000)

    monkeypatch.setattr(session, "ask_json", broke)
    r = c.post(f"/tracker/{jid}/session/ask", follow_redirects=True)
    page = r.data.decode()
    assert "model budget is used up" in page and "raise it in settings" in page
    assert "Traceback" not in page and "BudgetExhausted" not in page
    st = c.get(f"/tracker/{jid}/session/status").get_json()
    assert st["finished"] and st["phase"] == "failed" and st["error"] == session.BUDGET_MESSAGE
    assert sessions.current(data, jid)["transcript"] == []  # nothing half-written
    write_yaml(data / "presence.yaml", {})
    r = c.post(f"/tracker/{jid}/session/ask", follow_redirects=True)
    assert b"set up a model in Setup, step 3 first" in r.data


def test_turn_never_repeats_and_rejects_meta_angles():
    ctx = _ctx()
    asked = ctx.transcript[0]["question"] if ctx.transcript else "Why this one, honestly?"

    def ask(system, prompt, schema):
        assert "Already asked" in prompt or not ctx.transcript
        return {"question": asked, "why": "This question is asked because their ad is about "
                "no-shows and it matters a lot to them today", "angle": "If the person confirms "
                "a story we can set enough to true", "angle_from": "answer:1", "enough": False}

    out = session.next_turn(ctx, ask)
    assert out["question"] != asked and out["question"]  # a repeat is swapped for a fresh one
    assert out["angle"] is None  # a note the model wrote to itself is not an angle
    assert not out["why"].endswith("?") and len(out["why"].split()) <= 16

    def ask_good(system, prompt, schema):
        return {"question": "What would you do in the first month?", "why": "because the team "
                "is three people", "angle": "Your reporting rebuild meets their line about "
                "owning the monthly numbers.", "angle_from": "answer:1", "enough": False}

    out = session.next_turn(ctx, ask_good)
    assert out["angle"] == {"text": "Your reporting rebuild meets their line about owning the "
                                    "monthly numbers.", "from": "answer:1"}


def test_company_voice_is_flagged_and_repair_runs_once():
    ctx = _ctx()
    drafts = []
    bad = "Hello,\nWe need someone who can make a number trustworthy.\nBest,\nAda"

    def ask(system, prompt, schema):
        if schema is session.DRAFT:
            drafts.append(prompt)
            return {"message": bad if len(drafts) == 1 else MESSAGE_OK}
        return _attribute(prompt)

    out = session.compose(ctx, "email", "plain", ask)
    assert len(drafts) == 2 and "company's voice" in drafts[1]
    assert out["draft"].startswith("Hello,\nI rebuilt")  # the repaired version stood
