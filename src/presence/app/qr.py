"""QR codes for links the phone should open. Pure Python, SVG only."""

from __future__ import annotations

import segno


def data_uri(text: str) -> str:
    return segno.make(text, error="m").svg_data_uri(scale=4, border=2, dark="#2a2622")
