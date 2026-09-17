"""The CV library: several versions, a default, previews, and the profile prefill."""

import json

from presence.app import cvs, server
from presence.app.config_io import read_yaml, write_yaml


def _pdf(text):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in text.splitlines():
        page.insert_text((72, y), line, fontsize=11)
        y += 16
    return doc.tobytes()


def test_add_default_rename_remove(tmp_path):
    a = cvs.add_cv(
        tmp_path,
        filename="AdaExample_AIEngineer.pdf",
        raw=_pdf("Ada Example\nada@example.org\nSkills: Python, PyTorch"),
    )
    assert a["label"] == "AdaExample AIEngineer" and a["kind"] == "pdf" and a["is_default"]
    assert a["fields"]["name"] == "Ada Example" and a["has_preview"]
    assert cvs.preview_path(tmp_path, a["id"]).exists()
    b = cvs.add_cv(tmp_path, text="Ada Example\nData analyst CV text", label="Analyst")
    assert b["kind"] == "txt" and not b["is_default"] and cvs.default_id(tmp_path) == a["id"]
    assert [c["id"] for c in cvs.list_cvs(tmp_path)] == [b["id"], a["id"]]  # newest first
    assert (
        cvs.set_default(tmp_path, b["id"]) and cvs.default_fields(tmp_path)["name"] == "Ada Example"
    )
    assert (
        cvs.rename(tmp_path, b["id"], "Data analyst")
        and cvs.get(tmp_path, b["id"])["label"] == "Data analyst"
    )
    assert not cvs.rename(tmp_path, b["id"], "  ")
    assert cvs.remove(tmp_path, b["id"]) and cvs.default_id(tmp_path) == a["id"]  # default moves
    assert (
        cvs.remove(tmp_path, a["id"])
        and cvs.list_cvs(tmp_path) == []
        and cvs.default_id(tmp_path) == ""
    )
    assert cvs.get(tmp_path, "../etc") is None


def test_empty_and_unreadable_are_refused(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        cvs.add_cv(tmp_path, text="   ")
    with pytest.raises(ValueError):
        cvs.add_cv(tmp_path, filename="x.pdf", raw=b"%PDF-not really")


def test_upload_fills_the_profile_until_confirmed(tmp_path):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    c = app.test_client()
    write_yaml(
        tmp_path / "profile.yaml",
        {
            "identity": {"name": "Your Name", "email": "you@example.org"},
            "skills": [],
            "confirmed": False,
        },
    )
    page = c.get("/").data.decode()
    assert 'value="Your Name"' not in page  # placeholders never show
    import io

    r = c.post(
        "/setup/cv",
        data={
            "cv": (io.BytesIO(_pdf("Ada Example\nada@example.org\nPython, Docker")), "cv.pdf"),
            "label": "AI engineer",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"CV read" in r.data and b"AI engineer added" in r.data
    prof = read_yaml(tmp_path / "profile.yaml")
    assert (
        prof["identity"]["name"] == "Ada Example" and prof["identity"]["email"] == "ada@example.org"
    )
    assert "python" in prof["skills"] and prof["confirmed"] is False
    page = c.get("/").data.decode()
    assert 'value="Ada Example"' in page and "AI engineer" in page and "default</span>" in page
    assert 'class="chips" data-name="skills"' in page
    assert 'name="skills" value="python, docker"' in page
    assert json.loads((tmp_path / "cv_draft.json").read_text())["fields"]["name"] == "Ada Example"
    # while unconfirmed, another person's CV replaces every fact — nothing of Ada is left
    c.post(
        "/setup/cv",
        data={"cv_text": "Someone Else\nother@example.org\nSQL, Excel", "label": "Analyst"},
        follow_redirects=True,
    )
    prof = read_yaml(tmp_path / "profile.yaml")
    assert prof["identity"] == {"name": "Someone Else", "email": "other@example.org"}
    assert prof["skills"] == ["sql", "excel"] and prof["contact"]["phone"] == ""
    # once confirmed, a new CV joins the library but the confirmed facts stay
    c.post(
        "/setup/profile",
        data={"name": "Ada Example", "email": "ada@example.org", "work_auth": "full",
              "skills": "python", "confirmed": "1"},
    )
    c.post("/setup/cv", data={"cv_text": "Third Person\nthird@example.org", "label": "Third"},
           follow_redirects=True)
    assert read_yaml(tmp_path / "profile.yaml")["identity"]["name"] == "Ada Example"
    third = next(x for x in cvs.list_cvs(tmp_path) if x["label"] == "Third")
    r = c.post(f"/cv/{third['id']}/fill", follow_redirects=True)  # explicit fill always works
    assert b"form filled from Third" in r.data
    assert read_yaml(tmp_path / "profile.yaml")["identity"]["name"] == "Third Person"
    c.post(f"/cv/{third['id']}/remove")
    assert len(cvs.list_cvs(tmp_path)) == 2
    # default switch, preview and file routes, rename, remove
    other = next(x for x in cvs.list_cvs(tmp_path) if x["label"] == "Analyst")
    assert c.post(f"/cv/{other['id']}/default", follow_redirects=True).status_code == 200
    assert cvs.default_id(tmp_path) == other["id"]
    first = next(x for x in cvs.list_cvs(tmp_path) if x["label"] == "AI engineer")
    assert c.get(f"/cv/{first['id']}/preview.png").status_code == 200
    assert c.get(f"/cv/{first['id']}/file").mimetype == "application/pdf"
    assert c.get("/cv/nope/file").status_code == 404
    c.post(f"/cv/{other['id']}/rename", data={"label": "Data analyst"})
    assert cvs.get(tmp_path, other["id"])["label"] == "Data analyst"
    c.post(f"/cv/{other['id']}/remove")
    assert cvs.default_id(tmp_path) == first["id"]


def test_tracker_remembers_which_cv(tmp_path):
    from presence.tracker import Candidate, Tracker

    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    c = app.test_client()
    a = cvs.add_cv(tmp_path, text="Ada Example\nCV", label="AI engineer")
    t = Tracker(tmp_path / "tracker.db")
    job, _ = t.ingest(
        Candidate(company="Acme", title="AI Engineer", url="https://x/1", source="greenhouse")
    )
    t.close()
    page = c.get("/tracker").data.decode()
    assert "AI engineer" in page and "0 applications with this one" in page
    c.post(f"/tracker/{job.id}/event", data={"kind": "applied", "cv": a["id"]})
    t = Tracker(tmp_path / "tracker.db")
    assert (
        t.get(job.id).extra == {"cv": a["id"], "cv_label": "AI engineer"}
        and t.get(job.id).status == "applied"
    )
    t.close()
    page = c.get("/tracker").data.decode()
    assert "1 application with this one" in page
