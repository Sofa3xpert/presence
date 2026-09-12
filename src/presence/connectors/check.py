"""W3 verification: run one real search through a connector into a fresh tracker.

    uv run python -m presence.connectors.check linkedin \
        "graduate machine learning engineer" "London, United Kingdom"

Prints what came back and how many rows the tracker accepted (dedupe applied).
Uses a temporary database; nothing is kept."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from presence.connectors import (
    base,
    linkedin,  # noqa: F401  (registers "linkedin")
)
from presence.tracker import Tracker


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    name, term = sys.argv[1], sys.argv[2]
    location = sys.argv[3] if len(sys.argv) > 3 else ""
    connector = base.create(name)
    try:
        postings = connector.search(base.SearchQuery(term=term, location=location, limit=10,
                                                     hours_old=72))
    except base.ConnectorError as exc:
        print("CONNECTOR ERROR:", exc)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        tracker = Tracker(Path(tmp) / "check.db")
        created = sum(1 for p in postings if tracker.ingest(p.candidate)[1])
        for p in postings[:10]:
            print(f"  {p.posted or '----------'} {p.company[:26]:26} | "
                  f"{p.title[:48]:48} | {p.location[:22]}")
        print(f"{len(postings)} postings from {name}; tracker accepted {created} "
              f"(dedupe dropped {len(postings) - created})")
        tracker.close()
    print("W3 CHECK PASS" if postings else "W3 CHECK FAIL (no postings)")
    return 0 if postings else 1


if __name__ == "__main__":
    sys.exit(main())
