"""Presence.app launcher.

Runs the local Presence app on 127.0.0.1 from a menu-bar icon and opens the
browser to it. Everything here is glue around the normal package: nothing is
sent anywhere, nothing runs in a terminal.

Pure helpers (no side effects) live at the top so they can be unit-tested
without a Mac, without rumps and without importing ``presence``:
``pick_port``, ``presence_answers``, ``needs_init``, ``gui_path``,
``log_path``, ``redirect_stdio``.

Environment knobs (developers only): ``PRESENCE_PORT`` preferred port,
``PRESENCE_DATA`` data folder (honoured by the package itself),
``PRESENCE_SMOKE=1`` headless self-test that prints ``SMOKE OK``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

PREFERRED_PORT = 8790
HOST = "127.0.0.1"
GUI_EXTRA_PATH = ("/opt/homebrew/bin", "/usr/local/bin")

# ---------------------------------------------------------------- helpers


def log_path(home: Path | None = None) -> Path:
    """~/Library/Logs/Presence.log — where a windowed app's output goes."""
    return (home or Path.home()) / "Library" / "Logs" / "Presence.log"


class _Tee:
    """Write to the log and, when the process has one, the original console."""

    def __init__(self, log_handle, console):
        self._log = log_handle
        self._console = console

    def write(self, text):
        self._log.write(text)
        if self._console is not None:
            try:
                self._console.write(text)
            except (OSError, ValueError):
                self._console = None
        return len(text)

    def flush(self):
        self._log.flush()
        if self._console is not None:
            try:
                self._console.flush()
            except (OSError, ValueError):
                self._console = None

    def isatty(self):
        return False


def redirect_stdio(log: Path):
    """Send stdout/stderr to ``log`` (keeping the console too when there is one).

    A Finder-launched app gets /dev/null as its console and a PyInstaller
    ``--windowed`` app may even get ``None``; PyMuPDF and werkzeug write to
    them, so both must always land somewhere readable. Returns the log file.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log, "a", buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = _Tee(handle, sys.__stdout__)
    sys.stderr = _Tee(handle, sys.__stderr__)
    return handle


def gui_path(current: str | None, extra: tuple[str, ...] = GUI_EXTRA_PATH) -> str:
    """PATH for a Finder-launched app: Homebrew/Local bins are not on it,
    and Presence looks for Ollama with ``shutil.which``."""
    parts = [p for p in (current or "").split(os.pathsep) if p]
    for candidate in reversed(extra):
        if candidate not in parts:
            parts.insert(0, candidate)
    return os.pathsep.join(parts)


def port_is_free(port: int, host: str = HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(preferred: int = PREFERRED_PORT, host: str = HOST) -> int:
    """``preferred`` when it is free, otherwise any free port from the OS."""
    if port_is_free(preferred, host):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def presence_answers(port: int, host: str = HOST, timeout: float = 1.0, opener=None) -> bool:
    """True when something on ``host:port`` answers 200 on /health or /."""
    opener = opener or urllib.request.urlopen
    for path in ("/health", "/"):
        try:
            with opener(f"http://{host}:{port}{path}", timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                if status == 200:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return False


def needs_init(data: Path) -> bool:
    """A data folder without presence.yaml has never been set up."""
    return not (data / "presence.yaml").exists()


def wait_for_port(port: int, host: str = HOST, tries: int = 100, pause: float = 0.1) -> bool:
    for _ in range(tries):
        try:
            socket.create_connection((host, port), timeout=0.2).close()
            return True
        except OSError:
            time.sleep(pause)
    return False


def url_for(port: int, host: str = HOST) -> str:
    return f"http://{host}:{port}/"


def resource_path(name: str) -> str:
    """A file shipped next to this script (plain checkout or frozen bundle)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


# ------------------------------------------------------------- behaviours


def smoke() -> int:
    """Headless self-test for CI: the frozen bundle can import every native
    dependency and render the first page."""
    import tempfile

    import cryptography.hazmat.bindings._rust  # noqa: F401
    import google.auth  # noqa: F401
    import google_auth_oauthlib.flow  # noqa: F401
    import pymupdf
    import segno  # noqa: F401
    from google.oauth2 import credentials, service_account  # noqa: F401

    import presence
    from presence.app import create_app
    from presence.cli import cmd_init

    data = Path(tempfile.mkdtemp(prefix="presence-smoke-"))
    cmd_init(data)
    client = create_app(data).test_client()
    resp = client.get("/", follow_redirects=True)
    assert resp.status_code == 200, resp.status_code
    assert b"<html" in resp.data.lower()
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "hello presence")
    text = pymupdf.open(stream=doc.tobytes(), filetype="pdf")[0].get_text()
    assert "hello presence" in text, text
    print("SMOKE OK", presence.__version__, f"pymupdf {pymupdf.__version__}", flush=True)
    return 0


def serve(data: Path, port: int) -> None:
    from presence.app.run import serve as run_serve  # scheduler + /health, same as `presence serve`

    run_serve(data, port=port, open_browser=False, block=True)


def run_menu_bar(data: Path, port: int) -> None:
    import rumps

    url = url_for(port)
    icon = resource_path("menubar.png")

    class PresenceApp(rumps.App):
        def __init__(self):
            super().__init__(
                "Presence",
                icon=icon if os.path.exists(icon) else None,
                title=None if os.path.exists(icon) else "P",
                template=True,
                quit_button="Quit Presence",
            )
            self.menu = ["Open Presence", "Open data folder", None]

        @rumps.clicked("Open Presence")
        def open_presence(self, _):
            webbrowser.open(url)

        @rumps.clicked("Open data folder")
        def open_data(self, _):
            subprocess.Popen(["open", str(data)])

    def open_when_ready(timer):
        if wait_for_port(port, tries=1, pause=0):
            timer.stop()
            print(f"Presence is up at {url}", flush=True)
            webbrowser.open(url)
        elif time.monotonic() - started > 60:
            timer.stop()
            print("Presence did not start within 60 s; see this log", flush=True)

    started = time.monotonic()
    rumps.Timer(open_when_ready, 0.25).start()
    PresenceApp().run()


def main() -> int:
    log = redirect_stdio(log_path())
    os.environ["PATH"] = gui_path(os.environ.get("PATH"))
    if os.environ.get("PRESENCE_SMOKE"):
        return smoke()

    from presence.cli import cmd_init
    from presence.core.paths import default_data_dir

    preferred = int(os.environ.get("PRESENCE_PORT") or PREFERRED_PORT)
    if not port_is_free(preferred) and presence_answers(preferred):
        print(f"Presence already running at {url_for(preferred)}; opening it", flush=True)
        webbrowser.open(url_for(preferred))
        return 0

    data = default_data_dir()
    if needs_init(data):
        cmd_init(data)
    port = pick_port(preferred)
    print(f"Presence starting: data {data}, port {port}", flush=True)
    threading.Thread(target=serve, args=(data, port), daemon=True).start()
    run_menu_bar(data, port)
    log.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
