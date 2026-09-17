"""Notes a person keeps about a company for one job: a page they read, a post,
a product detail — pasted in their own time. Presence never fetches these.

    company/<job_id>/<n>.json — {"n", "title", "text", "added_at"}
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _dir(data: Path, job_id: int) -> Path:
    return data / "company" / str(int(job_id))


def list_notes(data: Path, job_id: int) -> list[dict[str, Any]]:
    d = _dir(data, job_id)
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.json"), key=lambda p: int(p.stem)):
        try:
            out.append(json.loads(f.read_text()))
        except ValueError:
            continue
    return out


def add(data: Path, job_id: int, text: str, title: str = "") -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("nothing to keep")
    d = _dir(data, job_id)
    d.mkdir(parents=True, exist_ok=True)
    n = max((int(p.stem) for p in d.glob("*.json")), default=0) + 1
    note = {"n": n, "title": (title or "").strip(), "text": text,
            "added_at": datetime.now().isoformat(timespec="seconds")}
    (d / f"{n}.json").write_text(json.dumps(note, ensure_ascii=False))
    return note


def remove(data: Path, job_id: int, n: int) -> bool:
    f = _dir(data, job_id) / f"{int(n)}.json"
    if not f.exists():
        return False
    f.unlink()
    return True


def text(data: Path, job_id: int) -> str:
    """Every note for the job, joined, for a prompt."""
    return "\n\n".join(
        (f"{n['title']}\n" if n.get("title") else "") + n["text"] for n in list_notes(data, job_id)
    ).strip()
