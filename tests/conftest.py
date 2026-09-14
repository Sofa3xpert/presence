"""Make the test suite independent of the editable install.

Presence uses a src layout. Normally `uv sync` installs the package in
editable mode through a `.pth` file — but Python 3.12 silently skips `.pth`
files that carry the macOS *hidden* flag, and some uv builds write them that
way (see scripts/doctor.py). Putting `src` on the path here means tests
always exercise the working tree, whatever the venv's mood.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# GitHub's macOS runners keep the application firewall on: an unsigned python that listens
# on 127.0.0.1 never gets its "accept incoming connections" prompt answered, so connects
# time out. Tests that must reach a live loopback server skip there, and run everywhere else.
needs_loopback = pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "darwin",
    reason="macOS runner firewall blocks connections to unsigned listeners",
)
