"""The person's CVs: several tailored versions, kept on this computer.

Layout under the data folder:
    cvs/index.json            {"default": "<id>"}
    cvs/<id>/original.pdf|txt the file as given (or the pasted text)
    cvs/<id>/text.txt         what Presence read from it
    cvs/<id>/preview.png      first page, PDFs only
    cvs/<id>/meta.json        id, label, filename, kind, added, chars, fields
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from presence.app.cvparse import extract_text, guess_fields

PREVIEW_WIDTH = 420


def cv_root(data: Path) -> Path:
    return data / "cvs"


def _index(data: Path) -> dict[str, Any]:
    f = cv_root(data) / "index.json"
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except ValueError:
        return {}


def _write_index(data: Path, index: dict[str, Any]) -> None:
    cv_root(data).mkdir(parents=True, exist_ok=True)
    (cv_root(data) / "index.json").write_text(json.dumps(index))


def default_id(data: Path) -> str:
    return str(_index(data).get("default") or "")


def _meta(d: Path) -> dict[str, Any] | None:
    f = d / "meta.json"
    try:
        return json.loads(f.read_text()) if f.exists() else None
    except ValueError:
        return None


def list_cvs(data: Path) -> list[dict[str, Any]]:
    """Newest first; each carries is_default and has_preview."""
    root = cv_root(data)
    if not root.exists():
        return []
    out = []
    default = default_id(data)
    for d in root.iterdir():
        m = _meta(d) if d.is_dir() else None
        if m:
            m["is_default"] = m["id"] == default
            m["has_preview"] = (d / "preview.png").exists()
            out.append(m)
    return sorted(out, key=lambda m: m.get("added", ""), reverse=True)


def get(data: Path, cv_id: str) -> dict[str, Any] | None:
    return _meta(cv_root(data) / cv_id) if _safe(cv_id) else None


def _safe(cv_id: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", cv_id or ""))


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "cv"


def label_from_filename(filename: str) -> str:
    stem = Path(filename or "").stem
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    return stem or "My CV"


def add_cv(data: Path, *, filename: str = "", raw: bytes | None = None, text: str | None = None,
           label: str = "") -> dict[str, Any]:
    """Store a CV (a PDF or text file, or pasted text). Raises ValueError when unreadable."""
    kind = "txt"
    if raw is not None:
        if (filename or "").lower().endswith(".pdf") or raw[:5] == b"%PDF-":
            kind = "pdf"
            try:
                text = extract_text(raw)
            except Exception as exc:
                raise ValueError("could not read that PDF") from exc
        else:
            text = raw.decode("utf-8", errors="replace")
    text = (text or "").strip()
    if not text:
        raise ValueError("that CV is empty")
    label = (label or "").strip() or label_from_filename(filename) or "My CV"
    cv_id = f"{_slug(label)}-{secrets.token_hex(2)}"
    d = cv_root(data) / cv_id
    d.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (d / f"original.{kind}").write_bytes(raw)
    else:
        (d / "original.txt").write_text(text)
    (d / "text.txt").write_text(text)
    fields = guess_fields(text)
    chars = int(fields.pop("text_chars", len(text)))
    if kind == "pdf":
        _preview(raw or b"", d / "preview.png")
    meta = {"id": cv_id, "label": label, "filename": filename or "", "kind": kind,
            "added": datetime.now().isoformat(),  # full precision keeps the order stable
            "chars": chars,
            "fields": fields}
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    if not default_id(data) or not get(data, default_id(data)):
        set_default(data, cv_id)
    return next((c for c in list_cvs(data) if c["id"] == cv_id), meta)


def _preview(raw: bytes, out: Path) -> None:
    try:
        import pymupdf

        doc = pymupdf.open(stream=raw, filetype="pdf")
        page = doc[0]
        zoom = PREVIEW_WIDTH / max(page.rect.width, 1)
        page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(str(out))
    except Exception:
        pass  # a missing preview is cosmetic


def set_default(data: Path, cv_id: str) -> bool:
    if not get(data, cv_id):
        return False
    index = _index(data)
    index["default"] = cv_id
    _write_index(data, index)
    return True


def rename(data: Path, cv_id: str, label: str) -> bool:
    m = get(data, cv_id)
    label = (label or "").strip()
    if not m or not label:
        return False
    m["label"] = label
    (cv_root(data) / cv_id / "meta.json").write_text(json.dumps(m, ensure_ascii=False))
    return True


def remove(data: Path, cv_id: str) -> bool:
    if not get(data, cv_id):
        return False
    shutil.rmtree(cv_root(data) / cv_id, ignore_errors=True)
    if default_id(data) == cv_id:
        rest = list_cvs(data)
        _write_index(data, {"default": rest[0]["id"] if rest else ""})
    return True


def save_extracted(data: Path, cv_id: str, extracted: dict[str, Any], fields: dict[str, Any],
                   model: str) -> None:
    """Keep the model's sectioned read next to the CV and promote its fields."""
    d = cv_root(data) / cv_id
    (d / "extracted.json").write_text(json.dumps(extracted, ensure_ascii=False))
    m = get(data, cv_id) or {}
    m["fields"] = fields
    m["read_with"] = model
    (d / "meta.json").write_text(json.dumps(m, ensure_ascii=False))


def default_fields(data: Path) -> dict[str, Any]:
    m = get(data, default_id(data))
    return dict(m.get("fields") or {}) if m else {}


def original_path(data: Path, cv_id: str) -> Path | None:
    d = cv_root(data) / cv_id
    for name in ("original.pdf", "original.txt"):
        if _safe(cv_id) and (d / name).exists():
            return d / name
    return None


def preview_path(data: Path, cv_id: str) -> Path | None:
    p = cv_root(data) / cv_id / "preview.png"
    return p if _safe(cv_id) and p.exists() else None
