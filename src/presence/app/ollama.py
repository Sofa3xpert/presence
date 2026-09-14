"""Ollama, the zero-cost path: detect it, point to the official installer,
pull a model with progress, and prove it can call a tool before calling it
ready. Never installs anything itself — installers want privileges, and a
page on localhost must not acquire them quietly."""

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
# Only tags known to exist and to support tool calling.
RECOMMENDATIONS = [(12, "llama3.2:3b", "about 2 GB"), (10**6, "qwen3.5:9b", "about 6 GB")]

_pulls: dict[str, dict[str, Any]] = {}


def system() -> str:
    return platform.system()


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


def server_status(base_url: str = DEFAULT_URL) -> dict[str, Any]:
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
    """Start an installed Ollama. Returns a short message; never raises."""
    sysname = sysname or system()
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


def status(base_url: str = DEFAULT_URL) -> dict[str, Any]:
    """The whole ladder in one call, for the page."""
    server = server_status(base_url)
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


def start_pull(model: str, base_url: str = DEFAULT_URL) -> dict[str, Any]:
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


def readiness(model: str, base_url: str = DEFAULT_URL) -> tuple[bool, str]:
    """Can this model drive the agent loop? The M1 echo-tool check, in-process."""
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
