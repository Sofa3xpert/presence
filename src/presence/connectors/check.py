"""Verification: read one real company board into a fresh tracker.

    uv run python -m presence.connectors.check greenhouse figma "Figma"
    uv run python -m presence.connectors.check lever palantir "Palantir"
    uv run python -m presence.connectors.check ashby openai "OpenAI"

Uses the provider's published API and a temporary database; nothing is kept."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from presence.connectors import base, boards  # noqa: F401  (boards registers providers)
from presence.core.config import SourceEntry
from presence.tracker import Tracker


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    provider, token = sys.argv[1], sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else token.title()
    src = SourceEntry(id=token, provider=provider, label=label, config={"board": token})
    try:
        postings = base.create(provider).fetch(src)
    except base.ConnectorError as exc:
        print("CONNECTOR ERROR:", exc)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        tracker = Tracker(Path(tmp) / "check.db")
        created = sum(1 for p in postings if tracker.ingest(p.candidate)[1])
        for p in postings[:8]:
            print(f"  {p.posted or '----------'} {p.title[:52]:52} | {p.location[:26]}")
        print(f"{len(postings)} postings from {provider}/{token}; tracker accepted {created}")
        tracker.close()
    print("CHECK PASS" if postings else "CHECK FAIL (no postings)")
    return 0 if postings else 1


if __name__ == "__main__":
    sys.exit(main())
