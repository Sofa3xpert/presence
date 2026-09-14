"""Presence's own copy of the local engine. Nothing to install, nothing to
type: the official standalone archive is downloaded into the data folder,
checked against the published checksum, unpacked, and run as a child of
the app on a private port — models live in the data folder too, so
removing that folder removes everything. If the person already runs
Ollama on its usual port, theirs is used and none of this happens.

Layout inside the data folder:
    engine/ollama/<version>/   the unpacked archive (plus its licence files)
    engine/ollama/current      pointer to the version in use
    engine/version.json        last release lookup (cached a day)
    engine/ollama.pid|.port    the running child
    models/                    OLLAMA_MODELS
    logs/ollama.log            the child's output
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import requests

RELEASES_LATEST = "https://api.github.com/repos/ollama/ollama/releases/latest"
RELEASE_FILE = "https://github.com/ollama/ollama/releases/download/v{version}/{name}"
VERSION_CACHE_SECONDS = 24 * 3600
SYSTEM_PORT = 11434
CHUNK = 1 << 20

# asset name and approximate size (MB) by platform.system() / platform.machine()
ASSETS: dict[tuple[str, str], tuple[str, int]] = {
    ("Darwin", "arm64"): ("ollama-darwin.tgz", 159),
    ("Darwin", "x86_64"): ("ollama-darwin.tgz", 159),
    ("Windows", "AMD64"): ("ollama-windows-amd64.zip", 1470),
    ("Windows", "ARM64"): ("ollama-windows-arm64.zip", 211),
    ("Linux", "x86_64"): ("ollama-linux-amd64.tar.zst", 1430),
    ("Linux", "aarch64"): ("ollama-linux-arm64.tar.zst", 1550),
}

_engine: Engine | None = None
_lock = threading.Lock()


def asset_for(sysname: str | None = None, machine: str | None = None) -> tuple[str, int] | None:
    key = (sysname or platform.system(), machine or platform.machine())
    return ASSETS.get(key)


def parse_checksums(text: str) -> dict[str, str]:
    """sha256sum.txt lines look like '<hex>  ./<asset>' → {asset: hex}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            out[parts[-1].split("/")[-1]] = parts[0].lower()
    return out


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def version_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in v.lstrip("v").split("-")[0].split("."))
    except ValueError:
        return (0,)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def port_is_free(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def api_version(port: int, timeout: float = 2) -> str | None:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/api/version", timeout=timeout)
        return str(r.json().get("version", "")) if r.status_code == 200 else None
    except Exception:
        return None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # the real OS decides how to ask, whatever platform.system() says
        return _cmdline(pid) != ""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # our own finished child shows up as a zombie until it is reaped
        return os.waitpid(pid, os.WNOHANG)[0] == 0
    except ChildProcessError:
        return True


def _cmdline(pid: int) -> str:
    """The full command line of a process, or '' when it is gone."""
    try:
        if os.name == "nt":
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], text=True, timeout=5
            )
            return out if str(pid) in out else ""
        proc = Path(f"/proc/{pid}/cmdline")  # Linux: exact, no width limit
        if proc.exists():
            return proc.read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        # -ww: unlimited width. Without it ps cuts at 80 columns when not on a terminal,
        # and a long install path made Presence disown its own engine.
        return subprocess.check_output(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)], text=True, timeout=5
        ).strip()
    except Exception:
        return ""


def _new_state() -> dict[str, Any]:
    return {
        "phase": "idle",
        "bytes_done": 0,
        "bytes_total": 0,
        "version": "",
        "error": None,
        "finished": False,
    }


class Engine:
    def __init__(self, data: Path) -> None:
        self.data = Path(data)
        self.root = self.data / "engine"
        self.versions = self.root / "ollama"
        self.models = self.data / "models"
        self.log = self.data / "logs" / "ollama.log"
        self.pidfile = self.root / "ollama.pid"
        self.portfile = self.root / "ollama.port"
        self.version_cache = self.root / "version.json"
        self.state = _new_state()
        self.proc: subprocess.Popen[bytes] | None = None
        self._busy = threading.Lock()
        self._installing = threading.Lock()
        self._hooked = False

    # ---- what is on disk -------------------------------------------------

    def version_dir(self, version: str) -> Path:
        return self.versions / version

    def binary_in(self, version_dir: Path, sysname: str | None = None) -> Path:
        sysname = sysname or platform.system()
        if sysname == "Windows":
            return version_dir / "ollama.exe"
        if sysname == "Linux":
            return version_dir / "bin" / "ollama"
        return version_dir / "ollama"

    def current_version(self) -> str | None:
        link = self.versions / "current"
        pointer = self.versions / "current.json"
        try:
            if link.is_symlink():
                name = os.readlink(link).rstrip("/").split("/")[-1]
            elif pointer.exists():
                name = json.loads(pointer.read_text()).get("version", "")
            else:
                return None
        except OSError:
            return None
        return name if name and self.binary_in(self.version_dir(name)).exists() else None

    def set_current(self, version: str) -> None:
        link = self.versions / "current"
        pointer = self.versions / "current.json"
        if os.name == "nt":  # no symlinks without privileges on Windows
            pointer.write_text(json.dumps({"version": version}))
            return
        tmp = self.versions / "current.tmp"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        os.symlink(version, tmp)
        os.replace(tmp, link)

    def installed_versions(self) -> list[str]:
        if not self.versions.exists():
            return []
        return sorted(
            (p.name for p in self.versions.iterdir()
             if p.is_dir() and not p.is_symlink() and self.binary_in(p).exists()),
            key=version_tuple,
        )

    def binary(self) -> Path | None:
        v = self.current_version()
        return self.binary_in(self.version_dir(v)) if v else None

    def installed(self) -> bool:
        return self.binary() is not None

    def models_size_bytes(self) -> int:
        total = 0
        if self.models.exists():
            for dirpath, _dirs, files in os.walk(self.models):
                for f in files:
                    try:
                        total += os.stat(os.path.join(dirpath, f)).st_size
                    except OSError:
                        pass
        return total

    # ---- release lookup, download, verify, unpack -------------------------

    def resolve_version(self, force: bool = False) -> str:
        cached: dict[str, Any] = {}
        try:
            if self.version_cache.exists():
                cached = json.loads(self.version_cache.read_text())
        except (OSError, json.JSONDecodeError):
            cached = {}
        fresh = time.time() - float(cached.get("checked_at", 0)) < VERSION_CACHE_SECONDS
        if cached.get("version") and fresh and not force:
            return str(cached["version"])
        try:
            r = requests.get(RELEASES_LATEST, timeout=15,
                             headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            version = str(r.json()["tag_name"]).lstrip("v")
        except Exception as exc:
            if cached.get("version"):
                return str(cached["version"])
            raise RuntimeError(f"could not look up the latest engine: {exc}") from exc
        asset = asset_for()
        self.root.mkdir(parents=True, exist_ok=True)
        self.version_cache.write_text(json.dumps({
            "version": version,
            "asset": asset[0] if asset else "",
            "checked_at": time.time(),
        }))
        return version

    def download(self, version: str, name: str, state: dict[str, Any]) -> Path:
        """Stream the archive to <name>.part, resuming with Range when possible."""
        self.versions.mkdir(parents=True, exist_ok=True)
        part = self.versions / f"{name}.part"
        url = RELEASE_FILE.format(version=version, name=name)
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        with requests.get(url, stream=True, timeout=(15, 120), headers=headers,
                          allow_redirects=True) as r:
            if r.status_code == 416:  # already complete
                state.update({"bytes_done": have, "bytes_total": have})
                return part
            r.raise_for_status()
            resume = r.status_code == 206 and have > 0
            total = int(r.headers.get("Content-Length") or 0)
            if resume:
                total += have
            else:
                have = 0
            state.update({"bytes_done": have, "bytes_total": total})
            with open(part, "ab" if resume else "wb") as f:
                for chunk in r.iter_content(CHUNK):
                    if not chunk:
                        continue
                    f.write(chunk)
                    have += len(chunk)
                    state["bytes_done"] = have
        return part

    def verify(self, version: str, name: str, archive: Path) -> str:
        url = RELEASE_FILE.format(version=version, name="sha256sum.txt")
        r = requests.get(url, timeout=30, allow_redirects=True)
        r.raise_for_status()
        expected = parse_checksums(r.text).get(name)
        if not expected:
            raise RuntimeError(f"no published checksum for {name}")
        actual = sha256_of(archive)
        if actual != expected:
            archive.unlink(missing_ok=True)
            raise RuntimeError("the downloaded engine did not match its checksum; try again")
        return actual

    def unpack(self, archive: Path, dest: Path, sysname: str | None = None) -> Path:
        sysname = sysname or platform.system()
        tmp = dest.with_name(dest.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        if sysname == "Windows":
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
        elif sysname == "Linux":
            import zstandard

            with open(archive, "rb") as f, zstandard.ZstdDecompressor().stream_reader(f) as r:
                with tarfile.open(fileobj=r, mode="r|") as t:
                    t.extractall(tmp, filter="data")
        else:
            with tarfile.open(archive, "r:gz") as t:
                t.extractall(tmp, filter="data")
        binary = self.binary_in(tmp, sysname)
        if not binary.exists():
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError("the engine archive did not contain the engine")
        if sysname != "Windows":
            binary.chmod(0o755)
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(tmp, dest)
        return dest

    def fetch_version(self, version: str, state: dict[str, Any]) -> Path:
        """Download + verify + unpack one version. Returns its directory."""
        asset = asset_for()
        if asset is None:
            raise RuntimeError(f"no engine build for {platform.system()} {platform.machine()}")
        name, _size = asset
        dest = self.version_dir(version)
        if self.binary_in(dest).exists():
            return dest
        state.update({"phase": "downloading", "version": version})
        archive = self.download(version, name, state)
        state["phase"] = "verifying"
        state["sha256"] = self.verify(version, name, archive)
        state["phase"] = "unpacking"
        self.unpack(archive, dest)
        archive.unlink(missing_ok=True)
        return dest

    # ---- the child process ---------------------------------------------

    def stale_pid(self) -> int | None:
        try:
            pid = int(self.pidfile.read_text().strip())
        except (OSError, ValueError):
            return None
        return pid if pid_alive(pid) else None

    def kill_stale(self) -> bool:
        """A child left behind by a killed app: ours only if it runs our engine."""
        pid = self.stale_pid()
        if pid is None:
            self.pidfile.unlink(missing_ok=True)
            return False
        if not self.owns(pid):
            self.pidfile.unlink(missing_ok=True)
            return False
        _terminate(pid)
        self.pidfile.unlink(missing_ok=True)
        return True

    def owns(self, pid: int) -> bool:
        """Is this pid running *our* engine binary (not the person's own Ollama)?"""
        cmd = _cmdline(pid)
        if os.name == "nt":  # tasklist reports the image name, not the path
            low = cmd.lower()
            return "ollama.exe" in low or str(self.versions).lower() in low
        return str(self.versions) in cmd

    def saved_port(self) -> int | None:
        try:
            return int(self.portfile.read_text().strip())
        except (OSError, ValueError):
            return None

    def pick_port(self) -> int:
        """Keep yesterday's port when it is still free, so saved settings stay valid."""
        old = self.saved_port()
        if old and old != SYSTEM_PORT and port_is_free(old):
            return old
        return free_port()

    def running_port(self) -> int | None:
        """The port of our own engine if it is up (this process's child or an
        adopted one from the pidfile)."""
        port = self.saved_port()
        if not port:
            return None
        if self.proc is not None and self.proc.poll() is None:
            return port if api_version(port, 0.5) is not None else None
        if self.stale_pid() is None:
            return None
        return port if api_version(port, 0.5) is not None else None

    def launch(self, timeout: float = 90) -> int:
        binary = self.binary()
        if binary is None:
            raise RuntimeError("the local engine is not installed yet")
        with self._busy:
            port = self.running_port()
            if port:
                return port
            self.kill_stale()
            port = self.pick_port()
            self.models.mkdir(parents=True, exist_ok=True)
            self.log.parent.mkdir(parents=True, exist_ok=True)
            env = {
                **os.environ,
                "OLLAMA_HOST": f"127.0.0.1:{port}",
                "OLLAMA_MODELS": str(self.models),
                "OLLAMA_KEEP_ALIVE": "10m",
            }
            kwargs: dict[str, Any] = {}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                )
            else:
                kwargs["start_new_session"] = True
            logf = open(self.log, "ab")
            try:
                self.proc = subprocess.Popen(
                    [str(binary), "serve"],
                    env=env,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    cwd=str(binary.parent),
                    **kwargs,
                )
            finally:
                logf.close()
            self.pidfile.write_text(str(self.proc.pid))
            self.portfile.write_text(str(port))
            self._hook_shutdown()
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    raise RuntimeError(
                        f"the local engine stopped right away (exit {self.proc.returncode})"
                    )
                if api_version(port, 1) is not None:
                    return port
                time.sleep(0.25)
            self.stop()
            raise RuntimeError("the local engine did not answer in time")

    def stop(self, wait: float = 10) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(wait)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(5)
        else:
            pid = self.stale_pid()
            if pid is not None and self.owns(pid):
                _terminate(pid, wait)
        self.pidfile.unlink(missing_ok=True)

    def _hook_shutdown(self) -> None:
        if self._hooked:
            return
        self._hooked = True
        atexit.register(self.stop)
        if threading.current_thread() is threading.main_thread():
            _install_signal_handler(self)

    # ---- the whole first-run flow, and upgrades -------------------------

    def install(self) -> dict[str, Any]:
        """Download, verify, unpack, point 'current' at it, launch. Blocking;
        a second caller while one runs just gets the live state."""
        state = self.state
        if not self._installing.acquire(blocking=False):
            return state
        try:
            return self._install(state)
        finally:
            self._installing.release()

    def _install(self, state: dict[str, Any]) -> dict[str, Any]:
        state.update(_new_state())
        state["phase"] = "resolving"
        try:
            version = self.resolve_version()
            self.fetch_version(version, state)
            self.set_current(version)
            state["phase"] = "starting"
            port = self.launch()
            state.update({"phase": "ready", "version": version, "port": port, "finished": True})
        except Exception as exc:
            state.update({"phase": "error", "error": str(exc)[:200], "finished": True})
        return state

    def install_in_background(self) -> dict[str, Any]:
        if not self._installing.locked():
            self.state.update(_new_state())
            self.state["phase"] = "resolving"
            threading.Thread(target=self.install, daemon=True).start()
        return self.state

    def check_update(self) -> dict[str, Any]:
        """Newer release? Fetch it, swap the pointer, restart, keep one previous."""
        current = self.current_version()
        latest = self.resolve_version(force=True)
        if current and version_tuple(latest) <= version_tuple(current):
            return {"updated": False, "current": current, "latest": latest}
        state = self.state
        state.update(_new_state())
        try:
            self.fetch_version(latest, state)
            was_running = self.running_port() is not None
            self.stop()
            self.set_current(latest)
            self.prune(keep=2)
            state.update({"phase": "ready", "finished": True, "version": latest})
            if was_running:
                self.launch()
        except Exception as exc:
            state.update({"phase": "error", "error": str(exc)[:200], "finished": True})
            return {"updated": False, "current": current, "latest": latest, "error": str(exc)}
        return {"updated": True, "current": latest, "previous": current, "latest": latest}

    def prune(self, keep: int = 2) -> list[str]:
        """Drop old version folders; keep the current one and one to fall back to."""
        current = self.current_version()
        versions = [v for v in self.installed_versions() if v != current]
        gone = versions[: max(0, len(versions) - (keep - 1))]
        for v in gone:
            shutil.rmtree(self.version_dir(v), ignore_errors=True)
        return gone

    # ---- one dict for the page --------------------------------------------

    def info(self) -> dict[str, Any]:
        asset = asset_for()
        port = self.running_port()
        if port:
            kind, version = "embedded", api_version(port, 1) or self.current_version() or ""
        elif (sysv := api_version(SYSTEM_PORT)) is not None:
            kind, version, port = "system", sysv, SYSTEM_PORT
        else:
            kind, version, port = "none", self.current_version() or "", None
        return {
            "kind": kind,
            "version": version,
            "port": port,
            "installed": self.installed(),
            "supported": asset is not None,
            "asset_size_mb": asset[1] if asset else None,
            "download": dict(self.state),
            "models_dir": str(self.models),
            "models_size_gb": round(self.models_size_bytes() / 2**30, 1),
        }

    def base_url(self) -> str:
        port = self.running_port()
        return f"http://127.0.0.1:{port}" if port else f"http://127.0.0.1:{SYSTEM_PORT}"


def _terminate(pid: int, wait: float = 10) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + wait
    while time.time() < deadline and pid_alive(pid):
        time.sleep(0.2)
    if pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


_previous_handlers: dict[int, Any] = {}


def _install_signal_handler(engine: Engine) -> None:
    def handler(signum: int, frame: Any) -> None:
        engine.stop()
        prev = _previous_handlers.get(signum)
        if callable(prev):
            prev(signum, frame)
        else:
            sys.exit(0)

    for sig in (signal.SIGTERM,) + ((signal.SIGHUP,) if hasattr(signal, "SIGHUP") else ()):
        try:
            _previous_handlers[sig] = signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


def bind(data: Path) -> Engine:
    """One engine per app process, living in this data folder."""
    global _engine
    with _lock:
        if _engine is None or _engine.data != Path(data):
            _engine = Engine(Path(data))
        return _engine


def current() -> Engine | None:
    return _engine
