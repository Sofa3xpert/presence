#!/usr/bin/env python3
"""Presence doctor — checks the development environment and fixes what it can.

    uv run --no-sync python scripts/doctor.py

Deliberately imports nothing from Presence, so it works precisely when the
package is broken. Currently knows one disease:

  The hidden-venv trap (macOS): uv marks `.venv` with the macOS *hidden*
  flag so it stays out of Finder. If the project lives in an iCloud-synced
  folder (Desktop or Documents with "Desktop & Documents Folders" on), the
  iCloud file provider propagates that flag to every file inside the venv —
  and Python 3.12+ silently skips any `.pth` file carrying it ("Skipping
  hidden .pth file" under `python -v`). Result: `import presence` fails while
  `uv pip list` swears it is installed. Upstream: astral-sh/uv#16977.

  Cure here: `chflags -R nohidden .venv`. Real fix: keep the repository out
  of iCloud-synced folders (e.g. ~/dev/presence).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import sysconfig
from pathlib import Path

UF_HIDDEN = getattr(stat, "UF_HIDDEN", 0)


def hidden(path: Path) -> bool:
    return bool(getattr(os.lstat(path), "st_flags", 0) & UF_HIDDEN)


def count_hidden(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if hidden(p)) + (1 if hidden(root) else 0)


def main() -> int:
    venv = Path(sys.prefix)
    site = Path(sysconfig.get_paths()["purelib"])
    print(f"venv: {venv}")
    problems = 0

    if UF_HIDDEN:
        flagged = count_hidden(venv)
        hidden_pths = [p for p in site.glob("*.pth") if hidden(p)]
        if flagged:
            problems += 1
            print(f"hidden-flagged files in venv: {flagged} "
                  f"({len(hidden_pths)} .pth — Python skips those)")
            subprocess.run(["chflags", "-R", "nohidden", str(venv)], check=False)
            left = count_hidden(venv)
            print("  chflags -R nohidden .venv ->", "fixed" if left == 0 else f"{left} still hidden")
            if str(venv).startswith(str(Path.home() / "Desktop")) or str(venv).startswith(
                str(Path.home() / "Documents")
            ):
                print("  NOTE: this project lives under Desktop/Documents. If iCloud syncs "
                      "those folders, the flag will keep coming back — move the repo "
                      "(e.g. ~/dev/presence). See scripts/doctor.py docstring.")
        else:
            print("hidden-flagged files in venv: 0")

    try:
        import presence  # noqa: F401

        print("import presence: OK (this interpreter)")
    except ModuleNotFoundError:
        if problems:
            print("import presence: will work in a fresh interpreter (site runs at startup)")
        else:
            problems += 1
            print("import presence: BROKEN and nothing hidden — try: "
                  "uv sync --reinstall-package presence")

    print("doctor:", "healthy" if problems == 0 else f"{problems} problem(s) treated/reported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
