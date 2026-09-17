"""Seen-postings store: every posting surfaced to the customer's Scout is
remembered by link key, so a judged-and-dropped posting never comes back on
tomorrow's run. JSON on disk, in the data directory."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path


class SeenPostings:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._seen: dict[str, str] = (
            json.loads(self.path.read_text()) if self.path.exists() else {}
        )

    def __contains__(self, key: str) -> bool:
        return key in self._seen

    def keys(self) -> set[str]:
        return set(self._seen)

    def mark(self, keys: list[str] | set[str], on: date | None = None) -> None:
        day = (on or date.today()).isoformat()
        for k in keys:
            self._seen.setdefault(k, day)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._seen, indent=0))

    def __len__(self) -> int:
        return len(self._seen)
