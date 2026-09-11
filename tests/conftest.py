"""Make the test suite independent of the editable install.

Presence uses a src layout. Normally `uv sync` installs the package in
editable mode through a `.pth` file — but Python 3.12 silently skips `.pth`
files that carry the macOS *hidden* flag, and some uv builds write them that
way (see scripts/doctor.py). Putting `src` on the path here means tests
always exercise the working tree, whatever the venv's mood.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
