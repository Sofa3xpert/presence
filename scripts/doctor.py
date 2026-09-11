#!/usr/bin/env python3
"""Presence doctor — checks the development environment and fixes what it can.

    uv run --no-sync python scripts/doctor.py

Deliberately imports nothing from Presence, so it works precisely when the
package is broken. Currently knows one disease:

  macOS hidden .pth — Python 3.12+ skips any .pth file whose macOS UF_HIDDEN
  flag is set ("Skipping hidden .pth file" under `python -v`). Some uv builds
  write the editable install's .pth with that flag, so `import presence`
  fails while `uv pip list` swears it is installed. Cure: chflags nohidden.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import sysconfig
from pathlib import Path


def hidden(path: Path) -> bool:
    return bool(getattr(os.lstat(path), "st_flags", 0) & getattr(stat, "UF_HIDDEN", 0))


def main() -> int:
    site = Path(sysconfig.get_paths()["purelib"])
    print(f"site-packages: {site}")
    problems = 0

    pths = sorted(site.glob("*.pth"))
    print(f".pth files: {len(pths)}")
    for pth in pths:
        if hidden(pth):
            problems += 1
            print(f"  HIDDEN (Python will skip it): {pth.name} — fixing with chflags nohidden")
            subprocess.run(["chflags", "nohidden", str(pth)], check=False)
            if hidden(pth):
                print("  still hidden — run manually: chflags nohidden", pth)
            else:
                print("  fixed")

    try:
        import presence  # noqa: F401  (after the fix, in a fresh interpreter this now works)

        print("import presence: OK (this interpreter)")
    except ModuleNotFoundError:
        if problems:
            print("import presence: will work in a fresh interpreter (site runs at startup)")
        else:
            problems += 1
            print("import presence: BROKEN and no hidden .pth found — try: "
                  "uv sync --reinstall-package presence")

    print("doctor:", "healthy" if problems == 0 else f"{problems} problem(s) treated/reported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
