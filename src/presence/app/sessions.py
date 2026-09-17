"""One message session per job and day, kept on disk beside the tracker.

    sessions/<job_id>/<date>.json — {
        "job_id", "date", "started", "stopped",
        "tone": "plain" | "warmer",
        "transcript": [{"n", "question", "why", "answer", "skipped"}],
        "angles": [{"n", "text", "from", "verdict": "" | "accepted" | "edited" | "rejected",
                    "final"}],
        "drafts": [{"channel", "tone", "draft", "sources", "issues", "at"}],
        "stories": {"use": [ids], "skip": [ids]},
    }

The functions below only shape the dictionary; nothing here talks to a model."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

VERDICTS = {"accept": "accepted", "edit": "edited", "reject": "rejected",
            "accepted": "accepted", "edited": "edited", "rejected": "rejected"}


def _dir(data: Path, job_id: int) -> Path:
    return data / "sessions" / str(int(job_id))


def path(data: Path, job_id: int, day: str) -> Path:
    return _dir(data, job_id) / f"{day}.json"


def new(job_id: int, today: date | None = None) -> dict[str, Any]:
    day = (today or date.today()).isoformat()
    return {"job_id": int(job_id), "date": day,
            "started": datetime.now().isoformat(timespec="seconds"), "stopped": False,
            "tone": "plain", "transcript": [], "angles": [], "drafts": [],
            "stories": {"use": [], "skip": []}}


def save(data: Path, job_id: int, sess: dict[str, Any]) -> Path:
    p = path(data, job_id, sess["date"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sess, ensure_ascii=False, indent=1))
    return p


def _read(p: Path) -> dict[str, Any] | None:
    try:
        sess = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(sess, dict):
        return None
    sess.setdefault("stories", {"use": [], "skip": []})
    for key in ("transcript", "angles", "drafts"):
        sess.setdefault(key, [])
    sess.setdefault("stopped", False)
    sess.setdefault("tone", "plain")
    return sess


def load(data: Path, job_id: int, day: str) -> dict[str, Any] | None:
    p = path(data, job_id, day)
    return _read(p) if p.exists() else None


def list_for(data: Path, job_id: int) -> list[dict[str, Any]]:
    """Every session for the job, newest first."""
    d = _dir(data, job_id)
    if not d.exists():
        return []
    out = [s for s in (_read(p) for p in sorted(d.glob("*.json"), reverse=True)) if s]
    return out


def current(data: Path, job_id: int, today: date | None = None) -> dict[str, Any]:
    """Today's session, else the latest one still open, else a fresh one (not yet saved)."""
    day = (today or date.today()).isoformat()
    sess = load(data, job_id, day)
    if sess is not None:
        return sess
    for s in list_for(data, job_id):
        if not s.get("stopped") or not s.get("drafts"):
            return s
    return new(job_id, today)


# ---------------------------------------------------------------- the transcript

def pending(sess: dict[str, Any]) -> dict[str, Any] | None:
    """The question waiting for an answer, if any."""
    for t in reversed(sess["transcript"]):
        if not t.get("answer") and not t.get("skipped"):
            return t
        break
    return None


def add_question(sess: dict[str, Any], question: str, why: str = "") -> dict[str, Any]:
    entry = {"n": len(sess["transcript"]) + 1, "question": question.strip(),
             "why": (why or "").strip(), "answer": "", "skipped": False}
    sess["transcript"].append(entry)
    return entry


def answer(sess: dict[str, Any], text: str) -> dict[str, Any] | None:
    t = pending(sess)
    if t is None or not (text or "").strip():
        return None
    t["answer"] = text.strip()
    t["skipped"] = False
    return t


def skip(sess: dict[str, Any]) -> dict[str, Any] | None:
    t = pending(sess)
    if t is None:
        return None
    t["skipped"] = True
    t["answer"] = ""
    return t


# ---------------------------------------------------------------- angles

def add_angle(sess: dict[str, Any], text: str, from_: str) -> dict[str, Any]:
    angle = {"n": len(sess["angles"]) + 1, "text": text.strip(), "from": from_,
             "verdict": "", "final": ""}
    sess["angles"].append(angle)
    return angle


def set_verdict(sess: dict[str, Any], n: int, verdict: str,
                text: str = "") -> dict[str, Any] | None:
    """accept | edit (with the person's wording) | reject. A rejected angle never reappears."""
    v = VERDICTS.get((verdict or "").strip().lower())
    if v is None:
        return None
    for a in sess["angles"]:
        if a["n"] == int(n):
            a["verdict"] = v
            a["final"] = text.strip() if v == "edited" and text.strip() else \
                (a["text"] if v == "accepted" else "")
            if v == "edited" and not a["final"]:
                a["verdict"], a["final"] = "accepted", a["text"]
            return a
    return None


def apply_turn(sess: dict[str, Any], turn: dict[str, Any]) -> None:
    """What next_turn found, into the session: an angle, a question, or a stop."""
    if turn.get("angle"):
        add_angle(sess, turn["angle"]["text"], turn["angle"]["from"])
    if turn.get("enough"):
        sess["stopped"] = True
    elif turn.get("question"):
        sess["stopped"] = False
        add_question(sess, turn["question"], turn.get("why", ""))


# ---------------------------------------------------------------- drafts and stories

def add_draft(sess: dict[str, Any], channel: str, tone: str,
              cleaned: dict[str, Any]) -> dict[str, Any]:
    draft = {"channel": channel, "tone": tone, "draft": cleaned.get("draft", ""),
             "sources": list(cleaned.get("sources") or []),
             "issues": list(cleaned.get("issues") or []),
             "at": datetime.now().isoformat(timespec="seconds")}
    sess["drafts"].append(draft)
    sess["tone"] = tone
    return draft


def story_verdict(sess: dict[str, Any], story_id: str, verdict: str) -> bool:
    """Tick a bank story in ("use") or out ("skip") for this session."""
    if verdict not in ("use", "skip") or not story_id:
        return False
    for key in ("use", "skip"):
        sess["stories"][key] = [s for s in sess["stories"][key] if s != story_id]
    sess["stories"][verdict].append(story_id)
    return True


def text_of(sess: dict[str, Any], answer_n: str = "", angle_n: str = "") -> str:
    """The words behind one answer or one kept angle — what "save to my stories" keeps."""
    if answer_n:
        for t in sess["transcript"]:
            if str(t["n"]) == str(answer_n) and t.get("answer"):
                return t["answer"]
    if angle_n:
        for a in sess["angles"]:
            if str(a["n"]) == str(angle_n) and a.get("verdict") != "rejected":
                return a.get("final") or a["text"]
    return ""
