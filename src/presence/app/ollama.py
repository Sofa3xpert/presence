"""Ollama, the zero-cost path: use the person's own Ollama if it is running,
otherwise Presence's embedded copy (see ollama_embedded) on a private port;
pull a model with progress, and prove it can call a tool before calling it
ready. Never runs an installer and never asks for privileges."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import requests

from presence.app import ollama_embedded

DEFAULT_URL = "http://127.0.0.1:11434"
DOWNLOADS = {
    "Darwin": "https://ollama.com/download/mac",
    "Windows": "https://ollama.com/download/windows",
    "Linux": "https://ollama.com/download/linux",
}
KNOWN_PATHS = {
    "Darwin": [
        "/Applications/Ollama.app/Contents/Resources/ollama",
        "/usr/local/bin/ollama",
        "/opt/homebrew/bin/ollama",
    ],
    "Linux": ["/usr/local/bin/ollama", "/usr/bin/ollama"],
    "Windows": [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    ],
}
# RAM ladder (GB ceiling, tag, download size). Only tags known to exist and to
# support tool calling; the echo-tool readiness gate stays the final arbiter.
RECOMMENDATIONS = [
    (6, "qwen3.5:2b", "about 2.7 GB"),
    (12, "qwen3.5:4b", "about 3.4 GB"),
    (24, "qwen3.5:9b", "about 6.6 GB"),
    (10**6, "qwen3.5:27b", "about 17 GB"),
]

_pulls: dict[str, dict[str, Any]] = {}


def system() -> str:
    return platform.system()


def bind(data: Path) -> ollama_embedded.Engine:
    """Tell this module which data folder holds the embedded engine."""
    return ollama_embedded.bind(data)


def engine() -> ollama_embedded.Engine | None:
    return ollama_embedded.current()


def resolve_base_url() -> str:
    """Where the server is right now: our embedded engine's private port when
    it is up, otherwise the usual 11434 (the person's own Ollama)."""
    eng = engine()
    return eng.base_url() if eng is not None else DEFAULT_URL


def download_url(sysname: str | None = None) -> str:
    return DOWNLOADS.get(sysname or system(), "https://ollama.com/download")


def installed_binary(sysname: str | None = None) -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    for path in KNOWN_PATHS.get(sysname or system(), []):
        if path and Path(path).exists():
            return path
    return None


def server_status(base_url: str | None = None) -> dict[str, Any]:
    base_url = base_url or resolve_base_url()
    try:
        version = requests.get(f"{base_url}/api/version", timeout=2).json().get("version", "")
        tags = requests.get(f"{base_url}/api/tags", timeout=4).json().get("models", [])
    except Exception:
        return {"running": False, "version": "", "models": []}
    return {
        "running": True,
        "version": version,
        "models": sorted(m.get("name", "") for m in tags if m.get("name")),
    }


def ram_gb() -> float | None:
    try:
        if system() == "Darwin":
            return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()) / 2**30
        if system() == "Linux":
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
        if system() == "Windows":
            import ctypes

            class Mem(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            m = Mem()
            m.dwLength = ctypes.sizeof(Mem)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
            return m.ullTotalPhys / 2**30
    except Exception:
        return None
    return None


def recommend_model(total_ram_gb: float | None) -> tuple[str, str]:
    if total_ram_gb is None:
        return RECOMMENDATIONS[-1][1], RECOMMENDATIONS[-1][2]
    for ceiling, name, size in RECOMMENDATIONS:
        if total_ram_gb < ceiling:
            return name, size
    return RECOMMENDATIONS[-1][1], RECOMMENDATIONS[-1][2]


def start(sysname: str | None = None) -> str:
    """Start the local engine: Presence's embedded copy when it is installed,
    else the person's own Ollama. Returns a short message; never raises."""
    sysname = sysname or system()
    eng = engine()
    if eng is not None and eng.installed():
        try:
            port = eng.launch()
            return f"the local engine is running on port {port}"
        except Exception as exc:
            return f"could not start the local engine: {str(exc)[:120]}"
    binary = installed_binary(sysname)
    try:
        if sysname == "Darwin" and Path("/Applications/Ollama.app").exists():
            subprocess.Popen(["open", "-a", "Ollama"])
            return "starting the Ollama app"
        if binary:
            subprocess.Popen(
                [binary, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return "starting `ollama serve`"
    except Exception as exc:
        return f"could not start Ollama: {type(exc).__name__}"
    return "Ollama is not installed"


def engine_info() -> dict[str, Any]:
    eng = engine()
    if eng is not None:
        return eng.info()
    asset = ollama_embedded.asset_for()
    kind = "system" if ollama_embedded.api_version(ollama_embedded.SYSTEM_PORT) else "none"
    return {
        "kind": kind,
        "version": "",
        "port": ollama_embedded.SYSTEM_PORT if kind == "system" else None,
        "installed": False,
        "supported": asset is not None,
        "asset_size_mb": asset[1] if asset else None,
        "download": {"phase": "idle", "bytes_done": 0, "bytes_total": 0, "version": "",
                     "error": None, "finished": False},
        "models_dir": "",
        "models_size_gb": 0.0,
    }


def status(base_url: str | None = None) -> dict[str, Any]:
    """The whole ladder in one call, for the page."""
    info = engine_info()
    url = base_url or (
        f"http://127.0.0.1:{info['port']}" if info.get("port") else DEFAULT_URL
    )
    server = server_status(url)
    ram = ram_gb()
    model, size = recommend_model(ram)
    return {
        **server,
        "installed": bool(installed_binary()),
        "download_url": download_url(),
        "system": system(),
        "ram_gb": round(ram, 1) if ram else None,
        "recommended": model,
        "recommended_size": size,
        "base_url": url,
        "engine": info,
    }


def parse_pull_line(line: str) -> dict[str, Any]:
    """One line of Ollama's streaming pull → {status, done_pct}."""
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return {"status": line.strip(), "done_pct": None}
    total, done = d.get("total"), d.get("completed")
    pct = round(100 * done / total, 1) if total and done is not None else None
    return {"status": d.get("status", ""), "done_pct": pct, "error": d.get("error")}


def _pull_worker(model: str, base_url: str) -> None:
    state = _pulls[model]
    try:
        with requests.post(
            f"{base_url}/api/pull",
            json={"name": model, "stream": True},
            stream=True,
            timeout=(10, 3600),
        ) as r:
            for raw in r.iter_lines():
                if not raw:
                    continue
                info = parse_pull_line(raw.decode())
                state.update(info)
                if info.get("error"):
                    state["finished"] = True
                    return
        state.update({"status": "success", "done_pct": 100.0, "finished": True})
    except Exception as exc:
        state.update({"error": f"{type(exc).__name__}: {str(exc)[:120]}", "finished": True})


def start_pull(model: str, base_url: str | None = None) -> dict[str, Any]:
    base_url = base_url or resolve_base_url()
    state = _pulls.get(model)
    if state and not state.get("finished"):
        return state
    _pulls[model] = {
        "model": model,
        "status": "starting",
        "done_pct": 0.0,
        "finished": False,
        "error": None,
    }
    threading.Thread(target=_pull_worker, args=(model, base_url), daemon=True).start()
    return _pulls[model]


def pull_status(model: str) -> dict[str, Any]:
    return _pulls.get(model) or {
        "model": model,
        "status": "not started",
        "done_pct": None,
        "finished": False,
        "error": None,
    }


def readiness(model: str, base_url: str | None = None) -> tuple[bool, str]:
    """Can this model drive the agent loop? The M1 echo-tool check, in-process."""
    base_url = base_url or resolve_base_url()
    from presence.core.executor import AgentSpec, Tool, run_agent
    from presence.core.providers import OpenAICompatProvider, ToolSpec

    seen: list[str] = []
    echo = Tool(
        spec=ToolSpec(
            name="echo",
            description="Echo the text back.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        handler=lambda text: (seen.append(text), f"echoed: {text}")[1],
    )
    try:
        result = run_agent(
            AgentSpec(
                name="ready",
                model=model,
                system="Follow the task exactly.",
                max_turns=4,
                max_output_tokens=200,
            ),
            "Call the echo tool once with text='presence'. Then reply DONE.",
            [echo],
            OpenAICompatProvider(base_url=f"{base_url}/v1"),
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:140]}"
    ok = result.stopped == "done" and any("presence" in s.lower() for s in seen)
    return ok, (
        f"tool call worked · {result.usage.total} tokens"
        if ok
        else "the model did not call the tool — pick a model that supports tools"
    )
