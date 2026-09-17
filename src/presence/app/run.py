"""Start the local app the way a person expects: one instance, a port that
works, the browser opened for them, and the daily run ticking in the
background for as long as Presence is open. Nothing here ever raises into
the server — problems go to a log file in the data folder."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import requests

from presence.core.config import ConfigError, load_app, load_profile, load_sources
from presence.core.scheduler import Job, Scheduler
from presence.cycle import run_cycle

PORTS = range(8790, 8800)
DEFAULT_AT = "08:00"
log = logging.getLogger("presence")

_servers: dict[str, threading.Thread] = {}
_schedulers: dict[str, threading.Thread] = {}


# ------------------------------------------------------------------ ports

def url_for_port(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def presence_at(port: int, timeout: float = 1.0) -> bool:
    """Does a Presence already answer on this port?"""
    try:
        r = requests.get(f"{url_for_port(port)}/health", timeout=timeout)
        return r.status_code == 200 and r.json().get("app") == "presence"
    except Exception:
        return False


def port_free(port: int) -> bool:
    """Can a server bind here? Checked the way the server itself binds (address
    reuse on), so connections still closing from the last Presence on this port
    do not make it look taken and push a restart onto a random port.

    Not on Windows: there address reuse lets a bind go through even past a live
    listener, so a taken port would look free — and a plain bind there already
    ignores connections that are only closing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if sys.platform != "win32":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def pick_port(preferred: int | None = None) -> int:
    """The preferred port, else the next free one of Presence's own range, else
    any free port — so the address stays predictable across restarts."""
    candidates = ([preferred] if preferred else []) + [p for p in PORTS if p != preferred]
    for port in candidates:
        if port_free(port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until_up(url: str, seconds: float = 15.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{url}/health", timeout=1).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


# -------------------------------------------------------------- readiness

def ready(data: Path) -> tuple[bool, str]:
    """May the daily run go ahead? Wording is what the person sees."""
    try:
        load_profile(data)
    except ConfigError as exc:
        return False, str(exc)
    try:
        sources = load_sources(data)
    except ConfigError as exc:
        return False, str(exc)
    if not any(s.enabled for s in sources):
        return False, "Add at least one company's careers page in Setup, step 2"
    return True, ""


def scout_at(data: Path) -> str:
    """The daily slot, HH:MM, from the runtime settings — or the default."""
    try:
        at = load_app(data).agents["scout"].at
        hour, minute = (int(x) for x in at.split(":"))
        if 0 <= hour < 24 and 0 <= minute < 60:
            return f"{hour:02d}:{minute:02d}"
    except Exception:
        pass
    return DEFAULT_AT


# -------------------------------------------------------------- scheduler

def build_scheduler(data: Path, clock: Callable[[], datetime] = datetime.now) -> Scheduler:
    async def cycle() -> None:
        await asyncio.to_thread(run_cycle, data, True)

    return Scheduler([Job("cycle", at=scout_at(data), run=cycle)], data / "scheduler.json",
                     clock=clock)


async def tick(data: Path, scheduler: Scheduler) -> list[str]:
    """One supervisor beat: refresh the slot, skip while setup is incomplete,
    otherwise let the scheduler decide. Never raises."""
    try:
        scheduler.jobs["cycle"].at = scout_at(data)
        ok, _why = ready(data)
        if not ok:
            return []
        return await scheduler.tick()
    except Exception:
        log.exception("the daily run could not be scheduled")
        return []


def _scheduler_loop(data: Path, interval: float) -> None:
    async def forever() -> None:
        scheduler = build_scheduler(data)
        while True:
            await tick(data, scheduler)
            await asyncio.sleep(interval)

    try:
        asyncio.run(forever())
    except Exception:
        log.exception("the daily run stopped")


def start_scheduler(data: Path, interval: float = 30.0) -> threading.Thread:
    key = str(data.resolve())
    thread = _schedulers.get(key)
    if thread is not None and thread.is_alive():
        return thread
    thread = threading.Thread(target=_scheduler_loop, args=(data, interval),
                              name="presence-scheduler", daemon=True)
    thread.start()
    _schedulers[key] = thread
    return thread


def scheduler_running(data: Path) -> bool:
    thread = _schedulers.get(str(data.resolve()))
    return thread is not None and thread.is_alive()


def next_run(data: Path, now: datetime | None = None) -> str:
    """A sentence for the Run page: when the next automatic run happens."""
    now = now or datetime.now()
    at = scout_at(data)
    if not scheduler_running(data):
        return f"automatic runs happen daily at {at} while Presence is open"
    ok, _why = ready(data)
    if not ok:
        return f"as soon as setup is complete (then daily at {at})"
    state: dict = {}
    path = data / "scheduler.json"
    try:
        state = json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        state = {}
    today = (state.get(now.date().isoformat()) or {}).get("cycle") or {}
    hour, minute = (int(x) for x in at.split(":"))
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    tomorrow = (slot + timedelta(days=1)).strftime("%a %H:%M")
    if today.get("status") == "ok":
        return f"tomorrow at {at} — today's run is done"
    if today.get("attempts", 0) >= 3:
        return f"{tomorrow} — today's attempts did not succeed"
    if now < slot:
        return f"today at {at}"
    return f"in a moment (it was due at {at})"


# ------------------------------------------------------------------ serve

def _setup_logging(data: Path) -> None:
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    if any(getattr(h, "_presence", False) for h in log.handlers):
        return
    handler = logging.FileHandler(logs / "presence.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler._presence = True  # type: ignore[attr-defined]
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def _open_when_up(url: str) -> None:
    if wait_until_up(url):
        try:
            opened = webbrowser.open(url)
            log.info("browser %s for %s", "opened" if opened else "not available", url)
        except Exception:
            log.exception("could not open the browser")


def server_thread(url: str) -> threading.Thread | None:
    """The thread serving this URL, if it was started by this process."""
    return _servers.get(url)


def serve(data: Path, port: int | None = None, open_browser: bool = True,
          block: bool = True) -> str:
    """Run the local app and the daily scheduler. Returns the app's URL.

    If a Presence already answers on the preferred port, that one is opened
    instead of starting a second copy."""
    from presence.app import create_app  # lazy: keeps `presence check` free of Flask

    preferred = port or PORTS[0]
    if presence_at(preferred):
        url = url_for_port(preferred)
        if open_browser:
            webbrowser.open(url)
        return url
    port = pick_port(port)
    url = url_for_port(port)
    _setup_logging(data)
    app = create_app(data)
    start_scheduler(data)
    log.info("Presence starting at %s", url)
    if open_browser:
        threading.Thread(target=_open_when_up, args=(url,), daemon=True).start()
    kwargs = {"host": "127.0.0.1", "port": port, "debug": False, "use_reloader": False}
    if block:
        app.run(**kwargs)
        return url
    thread = threading.Thread(target=app.run, kwargs=kwargs, name="presence-app", daemon=True)
    thread.start()
    _servers[url] = thread
    wait_until_up(url)
    return url
