"""The local app through Flask's test client — setup, CV, boards, tracker, run."""

import io
import json
import re

import pytest

from presence.app import ollama, server
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
    assert b"can&#39;t read that site yet" in r.data or b"can't read that site yet" in r.data


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


def _ready(c):
    c.post("/setup/profile", data={"name": "Ada", "work_auth": "full right", "confirmed": "1"})
    c.post("/sources/add", data={"url": "https://boards.greenhouse.io/acme"})


def test_run_page_uses_the_shared_cycle(client, monkeypatch):
    c, data = client
    _ready(c)
    monkeypatch.setattr(
        server, "run_cycle", lambda d, send=False: ("Presence · brief", {"x": "boom"}, False)
    )
    r = c.post("/run", data={})
    assert r.status_code == 200 and b"Presence" in r.data and b"boom" in r.data
    assert b"here is your brief" in r.data
    (data / "last_brief.txt").write_text("saved brief")
    assert b"saved brief" in c.get("/run").data


def test_run_page_gates_and_explains(client, monkeypatch):
    c, data = client
    page = c.get("/run").data.decode()
    assert "Confirm your profile first in Setup, step 4" in page
    assert re.search(r"<button disabled[^>]*>Run now", page)
    assert "Next automatic run:" in page
    r = c.post("/run", data={}, follow_redirects=True)
    assert b"Confirm your profile first" in r.data  # refused, not run
    c.post("/setup/profile", data={"name": "Ada", "work_auth": "full right", "confirmed": "1"})
    page = c.get("/run").data.decode()
    assert "careers page in Setup, step 5" in page
    c.post("/sources/add", data={"url": "https://boards.greenhouse.io/acme"})
    page = c.get("/run").data.decode()
    assert not re.search(r"<button disabled[^>]*>Run now", page)
    assert "pair Telegram in Setup, step 2" in page  # send button off while unpaired
    monkeypatch.setattr(server, "run_cycle", lambda d, send=False: ("brief", {}, False))
    r = c.post("/run", data={"send": "1"})
    assert b"Telegram is not paired, so the brief is shown here" in r.data
    (data / "secrets.env").write_text("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=1\n")
    page = c.get("/run").data.decode()
    assert "pair Telegram" not in page and "Telegram is paired" in page
    monkeypatch.setattr(server, "run_cycle", lambda d, send=False: ("brief", {}, True))
    assert b"sent to Telegram" in c.post("/run", data={"send": "1"}).data


def test_empty_boards_run_is_a_plain_message_not_a_crash(client):
    c, data = client
    c.post("/setup/profile", data={"name": "Ada", "work_auth": "full right", "confirmed": "1"})
    r = c.post("/run", data={}, follow_redirects=True)
    assert r.status_code == 200
    assert b"Add at least one company" in r.data and b"Traceback" not in r.data


def test_health_and_no_external_hosts_in_pages(client):
    c, data = client
    assert c.get("/health").get_json()["app"] == "presence"
    _ready(c)
    for path in ("/", "/tracker", "/run"):
        html = c.get(path).data.decode()
        hosts = set(re.findall(r"https?://([\w.-]+)", html))
        # allowed: the person's own links (Telegram, Ollama download) as plain anchors only;
        # nothing the page itself loads (fonts, scripts, styles) may leave the machine
        loaded = re.findall(r"""(?:src|href)=["'](https?://[^"']+)""", html)
        loaded = [u for u in loaded if 'rel="noopener"' not in html.split(u)[1][:80]]
        assert not loaded, (path, loaded)
        assert "fonts.googleapis.com" not in hosts and "gstatic" not in html
    css = c.get("/static/style.css").data.decode()
    assert "http" not in css and css.count("@font-face") >= 3
    assert c.get("/static/fonts/youngserif-400-latin.woff2").status_code == 200
    assert b"SIL OPEN FONT LICENSE" in c.get("/static/fonts/OFL.txt").data


def test_steps_are_done_only_when_saved(client, monkeypatch):
    c, data = client
    monkeypatch.setattr(ollama, "status", lambda base_url=None: {"running": False, "models": []})
    server._model_cache["key"] = None

    def state():
        return server._state(data)

    assert not state()["filters_done"] and not state()["sources_done"]
    assert not state()["model_done"]
    c.post("/setup/filters", data={"locations": "", "title_include": "", "freshness_days": "14"})
    assert state()["filters_done"]
    c.post("/sources/add", data={"url": "https://boards.greenhouse.io/acme"})
    assert state()["sources_done"]
    c.post("/sources/acme/toggle")
    assert not state()["sources_done"]
    c.post("/setup/model", data={"kind": "local", "model": "qwen3.5:9b"})
    assert not state()["model_done"]  # a name alone is not a working model
    monkeypatch.setattr(ollama, "status",
                        lambda base_url=None: {"running": True, "models": ["qwen3.5:9b"]})
    assert not state()["model_done"]  # cached for a few seconds
    server._model_cache["at"] = 0
    assert state()["model_done"]
    r = c.post("/setup/model", data={"kind": "anthropic", "api_key": "sk-x"}, follow_redirects=True)
    assert b"model saved" in r.data and b"anthropic \xc2\xb7" not in r.data
    assert state()["model_done"]


def test_broken_settings_file_renders_a_plain_error_page(client):
    c, data = client
    (data / "profile.yaml").write_text("identity: [unclosed")
    r = c.get("/")
    assert r.status_code == 500
    html = r.data.decode()
    assert "Something went wrong reading your settings." in html
    assert "profile.yaml" in html and "Copy details" in html and "Back to setup" in html
    assert "Open data folder" in html and "Traceback" not in html
    (data / "profile.yaml").write_text("identity: {name: Ada}\nconfirmed: true\n")
    (data / "cv_draft.json").write_text("{oops")
    r = c.get("/")
    assert r.status_code == 500 and b"cv_draft.json" in r.data
    assert c.get("/nope").status_code == 404  # ordinary HTTP errors stay themselves


def test_config_error_is_a_flash_with_a_link(client, monkeypatch):
    c, data = client
    from presence.core.config import ConfigError

    def boom(job_id):
        raise ConfigError("Confirm your profile first in Setup, step 4")

    monkeypatch.setattr(server.Tracker, "list", lambda self, status=None: boom(0))
    r = c.get("/tracker", follow_redirects=True)
    assert r.status_code == 200 and b"Confirm your profile first" in r.data
    assert b'href="/">open Setup</a>' in r.data


def test_open_data_folder_button(client, monkeypatch):
    c, data = client
    calls = []
    monkeypatch.setattr(server.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    r = c.post("/data/open", data={"back": "/run"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/run")
    assert calls and calls[0][-1] == str(data)
    assert calls[0][0] in ("open", "explorer", "xdg-open")


def test_cv_from_text_file_and_pasted_text(client):
    c, data = client
    txt = b"Ada Example\nada@example.org\nSkills: Python, SQL"
    r = c.post("/setup/cv", data={"cv": (io.BytesIO(txt), "cv.txt")},
               content_type="multipart/form-data", follow_redirects=True)
    assert b"CV read" in r.data
    fields = json.loads((data / "cv_draft.json").read_text())["fields"]
    assert fields["name"] == "Ada Example" and "sql" in fields["skills"]
    r = c.post("/setup/cv", data={"cv_text": "Bob Pasted\nbob@example.org"},
               follow_redirects=True)
    assert b"CV read" in r.data
    assert json.loads((data / "cv_draft.json").read_text())["fields"]["name"] == "Bob Pasted"
    r = c.post("/setup/cv", data={"cv": (io.BytesIO(b"%PDF-broken"), "cv.pdf")},
               content_type="multipart/form-data", follow_redirects=True)
    assert b"could not read that file" in r.data and b"paste the text" in r.data
    r = c.post("/setup/cv", data={}, follow_redirects=True)
    assert b"choose a file, or paste your CV text" in r.data
