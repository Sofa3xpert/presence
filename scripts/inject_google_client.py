#!/usr/bin/env python3
"""Put the release Google OAuth client into the source tree before packaging.

Reads PRESENCE_GOOGLE_CLIENT_ID and PRESENCE_GOOGLE_CLIENT_SECRET from the
environment and rewrites the two assignments in
``src/presence/adapters/google_client.py``. Running it twice is harmless: the
second run finds the values already in place and changes nothing. Meant for the
release workflow (values come from repository secrets); never commit the result.

    python scripts/inject_google_client.py            # inject
    python scripts/inject_google_client.py --check    # 0 if a client is in place
    python scripts/inject_google_client.py --file X   # another copy of the module
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "src/presence/adapters/google_client.py"
FIELDS = {
    "CLIENT_ID": "PRESENCE_GOOGLE_CLIENT_ID",
    "CLIENT_SECRET": "PRESENCE_GOOGLE_CLIENT_SECRET",
}


def _pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf'^{name} = "(?P<value>[^"\n]*)"$', re.M)


def current(text: str) -> dict[str, str]:
    out = {}
    for name in FIELDS:
        m = _pattern(name).search(text)
        if m is None:
            raise SystemExit(f"{name} assignment not found — is this google_client.py?")
        out[name] = m.group("value")
    return out


def inject(path: Path, values: dict[str, str]) -> bool:
    """Write the values in; True when the file changed."""
    text = path.read_text()
    current(text)  # validates the shape
    new = text
    for name, value in values.items():
        if '"' in value or "\n" in value:
            raise SystemExit(f"{name}: value contains a quote or newline")
        new = _pattern(name).sub(f'{name} = "{value}"', new, count=1)
    if new == text:
        return False
    path.write_text(new)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", type=Path, default=DEFAULT)
    ap.add_argument("--check", action="store_true", help="report whether a client is in place")
    args = ap.parse_args(argv)
    if args.check:
        vals = current(args.file.read_text())
        ok = not vals["CLIENT_ID"].startswith("__")
        print("google client: " + ("in place" if ok else "placeholders (source build)"))
        return 0 if ok else 1
    values = {}
    for name, env in FIELDS.items():
        value = os.environ.get(env, "").strip()
        if not value:
            print(f"{env} is not set", file=sys.stderr)
            return 2
        values[name] = value
    changed = inject(args.file, values)
    print("google client: " + ("injected" if changed else "already in place"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
