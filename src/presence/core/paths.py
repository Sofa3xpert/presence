"""Where Presence keeps a person's data when they never choose a folder.

macOS   ~/Library/Application Support/Presence
Windows %APPDATA%\\Presence
Linux   $XDG_DATA_HOME/presence (default ~/.local/share/presence)
PRESENCE_DATA overrides all of them."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_data_dir(platform: str | None = None, env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    platform = platform or sys.platform
    if env.get("PRESENCE_DATA"):
        return Path(env["PRESENCE_DATA"]).expanduser()
    home = Path(env.get("HOME") or Path.home()) if platform != "win32" else Path.home()
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Presence"
    if platform == "win32":
        base = env.get("APPDATA")
        return (Path(base) if base else home / "AppData" / "Roaming") / "Presence"
    base = env.get("XDG_DATA_HOME")
    return (Path(base) if base else home / ".local" / "share") / "presence"


def resolve_data_dir(given: Path | None) -> Path:
    return Path(given).expanduser() if given else default_data_dir()
