#!/usr/bin/env python3
"""Store a downloaded Google OAuth client file as the two GitHub repository secrets.

    python3 scripts/set_google_secrets.py ~/Downloads/client_secret_xxx.json [owner/repo]

Reads installed.client_id and installed.client_secret from the file and hands each
to `gh secret set` on its standard input. Prints only the secret names and whether
each was stored — never a value. Delete the JSON file afterwards."""

from __future__ import annotations

import json
import subprocess
import sys

NAMES = ("PRESENCE_GOOGLE_CLIENT_ID", "PRESENCE_GOOGLE_CLIENT_SECRET")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    path, repo = argv[1], (argv[2] if len(argv) > 2 else "Sofa3xpert/presence")
    try:
        with open(path, encoding="utf-8") as f:
            client = json.load(f).get("installed") or {}
    except (OSError, ValueError) as exc:
        print(f"could not read {path}: {type(exc).__name__}")
        return 1
    values = (client.get("client_id", ""), client.get("client_secret", ""))
    if not all(values):
        print("that is not a Desktop-app client file (no installed.client_id / client_secret)")
        return 1
    failed = 0
    for name, value in zip(NAMES, values, strict=True):
        r = subprocess.run(["gh", "secret", "set", name, "-R", repo], input=value, text=True,
                           capture_output=True)
        ok = r.returncode == 0
        failed += not ok
        print(f"{name}: {'stored' if ok else 'FAILED — ' + r.stderr.strip()[:200]}")
    if not failed:
        print(f"both secrets stored in {repo} — you can delete {path} now")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
