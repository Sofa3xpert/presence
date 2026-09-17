"""Regenerate Presence.icns and menubar.png from the SVG sources.

    uv run --no-sync python packaging/macos/make_icons.py

Uses PyMuPDF (already a Presence dependency) to rasterise the SVGs, then the
macOS tools ``sips`` and ``iconutil``. Run on a Mac; the outputs are committed
so builds never need this step.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
ICON_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def render(svg: Path, png: Path, size: int) -> None:
    doc = pymupdf.open(str(svg))
    page = doc[0]
    zoom = size / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
    pix.save(str(png))


def main() -> int:
    if sys.platform != "darwin":
        print("make_icons.py needs macOS (sips, iconutil)")
        return 1
    work = Path(tempfile.mkdtemp(prefix="presence-icons-"))
    master = work / "master.png"
    render(HERE / "Presence.svg", master, 1024)
    iconset = work / "Presence.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            subprocess.run(
                ["sips", "-z", str(px), str(px), str(master), "--out", str(iconset / name)],
                check=True, capture_output=True,
            )
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "Presence.icns")], check=True
    )
    render(HERE / "menubar.svg", HERE / "menubar.png", 44)
    shutil.rmtree(work, ignore_errors=True)
    print("wrote", HERE / "Presence.icns", "and", HERE / "menubar.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
