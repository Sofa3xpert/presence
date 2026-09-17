"""Read and write the data folder's YAML and secrets on behalf of the app."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def read_secrets(data: Path) -> dict[str, str]:
    path = data / "secrets.env"
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def write_secret(data: Path, key: str, value: str) -> None:
    path = data / "secrets.env"
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [ln for ln in lines if not ln.startswith(f"{key}=")]
    if value:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
