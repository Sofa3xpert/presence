"""The job description for a tracked job, kept as plain text beside the tracker.

    jd/<job_id>.txt — fetched from the board's published API at ingest, or pasted.
"""

from __future__ import annotations

from pathlib import Path


def path(data: Path, job_id: int) -> Path:
    return data / "jd" / f"{int(job_id)}.txt"


def save(data: Path, job_id: int, text: str) -> None:
    p = path(data, job_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text((text or "").strip() + "\n")


def load(data: Path, job_id: int) -> str:
    p = path(data, job_id)
    return p.read_text().strip() if p.exists() else ""


def has(data: Path, job_id: int) -> bool:
    return bool(load(data, job_id))
