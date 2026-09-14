"""Secret lookup, strictly local (charter rule 3): environment variable
first, then the OS keyring when available, then a strict-permission
`secrets.env` file in the data directory. Secrets are never logged."""

from __future__ import annotations

import os
import stat
import warnings
from pathlib import Path


def _from_keyring(name: str) -> str | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        return keyring.get_password("presence", name)
    except Exception:
        return None


def _from_env_file(name: str, data_dir: Path) -> str | None:
    path = data_dir / "secrets.env"
    if not path.exists():
        return None
    # POSIX file modes only; Windows reports 0o666 for everything and protects
    # the folder through the account instead.
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        warnings.warn(
            f"{path.name} in your Presence data folder can be read by other accounts "
            "on this computer. Presence still works; consider restricting the file "
            "to your own account.",
            stacklevel=2,
        )
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip()
    return None


def get_secret(name: str, data_dir: Path | None = None) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    value = _from_keyring(name)
    if value:
        return value
    if data_dir is not None:
        return _from_env_file(name, data_dir)
    return None
