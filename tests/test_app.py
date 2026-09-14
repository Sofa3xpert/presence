"""The local app through Flask's test client — setup, CV, boards, tracker, run."""

import io
import json

import pytest

from presence.app import server
from presence.app.boards_ui import detect
from presence.app.config_io import read_secrets, read_yaml
from presence.core.config import load_profile, load_sources
from presence.tracker import Candidate, Tracker


@pytest.fixture
def client(tmp_path):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client(), tmp_path


def _pdf(text: str) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in text.splitlines():
        page.insert_text((72, y), line, fontsize=11)
        y += 16
    return doc.tobytes()


def test_detect_careers_urls():
    assert detect("https://boards.greenhouse.io/figma/jobs/123") == ("greenhouse", "figma")
    assert detect("https://job-boards.greenhouse.io/anthropic") == ("greenhouse", "anthropic")
    assert detect("https://jobs.lever.co/palantir/abc") == ("lever", "palantir")
    assert detect("https://jobs.ashbyhq.com/openai") == ("ashby", "openai")
    assert detect("https://apply.workable.com/huggingface/") == ("workable", "huggingface")
    assert detect("https://jobs.smartrecruiters.com/Wise/123") == ("smartrecruiters", "Wise")
    assert detect("https://www.linkedin.com/jobs/view/1") is None


def test_setup_renders_and_boards_roundtrip(client):
    c, data = client
    assert c.get("/").status_code == 200
    r = c.post(
        "/sources/add", data={"url": "https://boards.greenhouse.io/figma"}, follow_redirects=True
    )
    assert b"added Figma (greenhouse)" in r.data
    c.post("/sources/add", data={"provider": "lever", "board": "palantir", "label": "Palantir"})
    srcs = load_sources(data)
    assert [(s.provider, s.config["board"], s.enabled) for s in srcs] == [
        ("greenhouse", "figma", True),
        ("lever", "palantir", True),
    ]
    c.post("/sources/figma/toggle")
    assert load_sources(data)[0].enabled is False
    c.post("/sources/figma/remove")
    assert [s.id for s in load_sources(data)] == ["palantir"]
    r = c.post(
        "/sources/add", data={"url": "https://www.linkedin.com/jobs/view/1"}, follow_redirects=True
    )
    assert b"isn&#39;t a board on a published API" in r.data or b"isn't a board" in r.data


def test_cv_upload_then_profile_confirm(client):
    c, data = client
    pdf = _pdf(
        "Ada Example\nada@example.org · +44 7000 000000\n"
        "github.com/ada\nSkills: Python, PyTorch, Docker"
    )
    r = c.post(
        "/setup/cv",
        data={"cv": (io.BytesIO(pdf), "cv.pdf")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"CV read" in r.data
    fields = json.loads((data / "cv_draft.json").read_text())["fields"]
    assert fields["name"] == "Ada Example" and fields["email"] == "ada@example.org"
    assert {"python", "pytorch", "docker"} <= set(fields["skills"])
    page = c.get("/").data.decode()
    assert 'value="Ada Example"' in page  # form prefilled from the draft
    # unconfirmed save keeps Presence from running
    c.post(
        "/setup/profile",
        data={
            "name": "Ada Example",
            "email": "ada@example.org",
            "work_auth": "full right to work",
            "skills": "python, pytorch",
        },
    )
    with pytest.raises(Exception, match="confirmed"):
        load_profile(data)
    r = c.post(
        "/setup/profile",
        data={
            "name": "Ada Example",
            "email": "ada@example.org",
            "work_auth": "full right to work",
            "skills": "python",
            "confirmed": "1",
        },
        follow_redirects=True,
    )
    assert b"profile confirmed" in r.data
    assert load_profile(data).identity.name == "Ada Example"


def test_model_tracker_and_filters_settings(client):
    c, data = client
    c.post("/setup/model", data={"kind": "anthropic", "api_key": "sk-test", "model": ""})
    cfg = read_yaml(data / "presence.yaml")
    assert cfg["providers"]["anthropic"]["api_key_secret"] == "ANTHROPIC_API_KEY"
    assert cfg["agents"]["scout"]["model"] == server.DEFAULT_MODELS["anthropic"]
    assert read_secrets(data)["ANTHROPIC_API_KEY"] == "sk-test"
    c.post("/setup/model", data={"kind": "local", "model": "qwen3.5:9b", "base_url": ""})
    assert read_yaml(data / "presence.yaml")["providers"]["local"]["base_url"].startswith(
        "http://localhost"
    )
    c.post("/setup/tracker", data={"backend": "sheet"})
    assert read_yaml(data / "presence.yaml")["tracker"]["backend"] == "sheet"
    c.post(
        "/setup/filters",
        data={
            "locations": "London, Remote",
            "title_include": "engineer",
            "title_exclude": "senior",
            "freshness_days": "7",
        },
    )
    search = read_yaml(data / "search.yaml")
    assert search["locations"] == ["London", "Remote"] and search["freshness_hours"] == 168


def test_tracker_page_and_events(client):
    c, data = client
    t = Tracker(data / "tracker.db")
    job, _ = t.ingest(
        Candidate(company="Acme", title="AI Engineer", url="https://x/1", source="greenhouse")
    )
    t.close()
    page = c.get("/tracker").data.decode()
    assert "Acme" in page and "AI Engineer" in page
    c.post(f"/tracker/{job.id}/event", data={"kind": "applied"})
    c.post(f"/tracker/{job.id}/event", data={"kind": "rejected"})
    r = c.post(f"/tracker/{job.id}/event", data={"kind": "applied"}, follow_redirects=True)
    assert b"only notes may be added to a closed job" in r.data
    assert "rejected" in c.get("/tracker?status=rejected").data.decode()


def test_run_page_uses_the_shared_cycle(client, monkeypatch):
    c, data = client
    monkeypatch.setattr(
        server, "run_cycle", lambda d, send=False: ("Presence · brief", {"x": "boom"}, False)
    )
    r = c.post("/run", data={})
    assert r.status_code == 200 and b"Presence" in r.data and b"boom" in r.data
    (data / "last_brief.txt").write_text("saved brief")
    assert b"saved brief" in c.get("/run").data
