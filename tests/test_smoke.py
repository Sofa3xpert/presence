"""Foundation smoke tests: the package imports and the repo keeps its soul."""

import re
from pathlib import Path

import presence

REPO = Path(__file__).resolve().parent.parent


def test_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", presence.__version__)


def test_charter_present_and_complete():
    charter = (REPO / "CHARTER.md").read_text()
    for rule in (
        "A human clicks every Submit",
        "No invented facts",
        "Data stays home",
        "Spending is capped",
    ):
        assert rule in charter, f"charter rule missing: {rule}"


def test_core_license_is_agpl():
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (REPO / "LICENSE").read_text()


def test_pack_formats_are_mit():
    assert "MIT License" in (REPO / "packs" / "LICENSE").read_text()
