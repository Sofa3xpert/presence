"""The stories bank: what the person has told Presence about themselves, in
their own words, kept so the third message is faster than the first.

    stories.json — [{"id", "text", "tags", "source", "job_id", "created", "confirmed"}]

A story from a session is confirmed by construction (they typed it). A story
drafted from a CV read is unconfirmed until the person keeps it (charter rule 2).
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any


def _file(data: Path) -> Path:
    return data / "stories.json"


def list_stories(data: Path, confirmed_only: bool = False) -> list[dict[str, Any]]:
    f = _file(data)
    try:
        items = json.loads(f.read_text()) if f.exists() else []
    except ValueError:
        items = []
    return [s for s in items if s.get("confirmed") or not confirmed_only]


def _write(data: Path, items: list[dict[str, Any]]) -> None:
    _file(data).write_text(json.dumps(items, ensure_ascii=False, indent=1))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def add(data: Path, text: str, *, tags: list[str] | tuple[str, ...] = (), source: str = "session",
        job_id: int | None = None, confirmed: bool = True) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not text:
        raise ValueError("an empty story")
    items = list_stories(data)
    for s in items:
        if _norm(s["text"]) == _norm(text):
            return s  # already known; never duplicate
    story = {"id": f"sto-{secrets.token_hex(3)}", "text": text, "tags": [t for t in tags if t],
             "source": source, "job_id": job_id,
             "created": datetime.now().isoformat(timespec="seconds"), "confirmed": bool(confirmed)}
    items.append(story)
    _write(data, items)
    return story


def get(data: Path, story_id: str) -> dict[str, Any] | None:
    return next((s for s in list_stories(data) if s["id"] == story_id), None)


def update(data: Path, story_id: str, *, text: str | None = None,
           tags: list[str] | None = None, confirmed: bool | None = None) -> bool:
    items = list_stories(data)
    for s in items:
        if s["id"] == story_id:
            if text is not None and text.strip():
                s["text"] = re.sub(r"\s+", " ", text).strip()
            if tags is not None:
                s["tags"] = [t for t in tags if t]
            if confirmed is not None:
                s["confirmed"] = bool(confirmed)
            _write(data, items)
            return True
    return False


def confirm(data: Path, story_id: str) -> bool:
    return update(data, story_id, confirmed=True)


def remove(data: Path, story_id: str) -> bool:
    items = list_stories(data)
    kept = [s for s in items if s["id"] != story_id]
    if len(kept) == len(items):
        return False
    _write(data, kept)
    return True


def seed_from_cv(data: Path, cv_id: str, facts: list[dict[str, str]]) -> int:
    """Draft one unconfirmed story per CV highlight; skips what is already known."""
    before = len(list_stories(data))
    for fact in facts:
        claim = (fact.get("claim") or "").strip()
        if len(claim) < 12:
            continue
        add(data, claim, tags=[fact.get("source", "")] if fact.get("source") else [],
            source=f"cv:{cv_id}", confirmed=False)
    return len(list_stories(data)) - before
