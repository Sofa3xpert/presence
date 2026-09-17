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
RECOMMENDATIONS = [  # one small default; bigger models stay a typed name away
    (6, "qwen3.5:2b", "about 2.7 GB"),
    (10**6, "nemotron-3-nano:4b", "about 2.8 GB"),
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
    global _served
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
            _served = subprocess.Popen(
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


# ---- the Models page: what is here, what is loaded, and how much room is left ----

_shows: dict[str, dict[str, Any]] = {}  # /api/show answers, remembered per digest
_served: subprocess.Popen[bytes] | None = None  # an `ollama serve` Presence started itself


def models_dir(info: dict[str, Any] | None = None) -> Path:
    """Where model files live: Presence's engine folder when its own engine is
    the one in play, otherwise the person's own Ollama store."""
    eng = engine()
    info = info or engine_info()
    if eng is not None and (info.get("kind") == "embedded"
                            or (info.get("kind") == "none" and info.get("installed"))):
        return eng.models
    env = os.environ.get("OLLAMA_MODELS")
    return Path(env) if env else Path.home() / ".ollama" / "models"


def disk_usage(path: Path) -> dict[str, float | None]:
    """Free and total space on the disk that holds ``path`` (or its nearest parent)."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        u = shutil.disk_usage(p)
    except OSError:
        return {"total_gb": None, "free_gb": None}
    return {"total_gb": round(u.total / 2**30, 1), "free_gb": round(u.free / 2**30, 1)}


def _model_row(name: str, size: int, digest: str = "", modified: str = "",
               details: dict[str, Any] | None = None) -> dict[str, Any]:
    d = details or {}
    return {"name": name, "size": int(size or 0), "digest": digest, "modified": modified,
            "family": d.get("family", ""), "params": d.get("parameter_size", ""),
            "quant": d.get("quantization_level", ""), "loaded": False, "memory": 0,
            "vram": 0, "expires": "", "context": None, "capabilities": []}


def list_models(base_url: str | None = None) -> list[dict[str, Any]]:
    """Installed models as the engine lists them (/api/tags)."""
    base_url = base_url or resolve_base_url()
    try:
        tags = requests.get(f"{base_url}/api/tags", timeout=4).json().get("models", [])
    except Exception:
        return []
    rows = [_model_row(m["name"], m.get("size") or 0, m.get("digest", ""),
                       str(m.get("modified_at") or "")[:10], m.get("details"))
            for m in tags if m.get("name")]
    return sorted(rows, key=lambda m: m["name"])


def loaded_models(base_url: str | None = None) -> dict[str, dict[str, Any]]:
    """Models resident in memory right now (/api/ps), by name."""
    base_url = base_url or resolve_base_url()
    try:
        ps = requests.get(f"{base_url}/api/ps", timeout=4).json().get("models", [])
    except Exception:
        return {}
    return {m["name"]: {"memory": int(m.get("size") or 0), "vram": int(m.get("size_vram") or 0),
                        "expires": str(m.get("expires_at") or "")[:19]}
            for m in ps if m.get("name")}


def show_model(name: str, digest: str = "", base_url: str | None = None) -> dict[str, Any]:
    """Context length and capabilities (/api/show), remembered per digest so a
    page refresh does not ask again."""
    if digest and digest in _shows:
        return _shows[digest]
    base_url = base_url or resolve_base_url()
    try:
        d = requests.post(f"{base_url}/api/show", json={"model": name}, timeout=6).json()
    except Exception:
        return {}
    info = d.get("model_info") or {}
    ctx = next((v for k, v in info.items() if str(k).endswith(".context_length")), None)
    out = {"context": int(ctx) if ctx else None,
           "capabilities": [str(c) for c in (d.get("capabilities") or [])]}
    if digest:
        _shows[digest] = out
    return out


def _engine_reply(r: Any) -> tuple[bool, str]:
    try:
        body = r.json() if r.content else {}
    except ValueError:
        body = {}
    err = body.get("error") if isinstance(body, dict) else ""
    if r.status_code != 200 or err:
        return False, str(err or f"the engine answered {r.status_code}")[:160]
    return True, "ok"


def load_model(name: str, base_url: str | None = None,
               keep_alive: str | int = -1) -> tuple[bool, str]:
    """Bring a model into memory and keep it there until it is unloaded: an empty
    generate with ``keep_alive`` — nothing is asked of the model."""
    base_url = base_url or resolve_base_url()
    try:
        r = requests.post(f"{base_url}/api/generate",
                          json={"model": name, "keep_alive": keep_alive}, timeout=(10, 600))
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"
    return _engine_reply(r)


def unload_model(name: str, base_url: str | None = None) -> tuple[bool, str]:
    """Free the memory a model holds (keep_alive 0)."""
    return load_model(name, base_url, keep_alive=0)


def delete_model(name: str, base_url: str | None = None) -> tuple[bool, str]:
    """Remove a model's files from this computer (/api/delete)."""
    base_url = base_url or resolve_base_url()
    try:
        r = requests.delete(f"{base_url}/api/delete", json={"model": name}, timeout=30)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"
    return _engine_reply(r)


def models_on_disk(root: Path) -> list[dict[str, Any]]:
    """Installed models read from the store itself, for when the engine is off:
    manifests/<registry>/<namespace>/<name>/<tag>; the size is its layers."""
    from datetime import date

    manifests = Path(root) / "manifests"
    rows: list[dict[str, Any]] = []
    if not manifests.exists():
        return rows
    for f in manifests.rglob("*"):
        parts = f.relative_to(manifests).parts
        if not f.is_file() or len(parts) < 4:
            continue
        try:
            d = json.loads(f.read_text())
            size = sum(int(layer.get("size") or 0) for layer in d.get("layers", []))
            added = date.fromtimestamp(f.stat().st_mtime).isoformat()
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        registry, ns, name, tag = parts[0], parts[-3], parts[-2], parts[-1]
        full = f"{name}:{tag}" if ns == "library" else f"{ns}/{name}:{tag}"
        if registry != "registry.ollama.ai":
            full = f"{registry}/{full}"
        rows.append(_model_row(full, size, modified=added))
    return sorted(rows, key=lambda m: m["name"])


def stop(sysname: str | None = None) -> str:
    """Stop the local engine: Presence's own copy when that is the one running,
    the `ollama serve` Presence started, or the Ollama app on a Mac. Anything
    else was started outside Presence and is left alone."""
    global _served
    sysname = sysname or system()
    info = engine_info()
    eng = engine()
    if info.get("kind") == "embedded" and eng is not None:
        try:
            eng.stop()
        except Exception as exc:
            return f"could not stop the local engine: {str(exc)[:120]}"
        return "the local engine is stopped — every loaded model is out of memory"
    if info.get("kind") != "system":
        return "the local engine is not running"
    if _served is not None and _served.poll() is None:
        _served.terminate()
        _served = None
        return "Ollama is stopped — every loaded model is out of memory"
    if sysname == "Darwin" and Path("/Applications/Ollama.app").exists():
        try:
            subprocess.run(["osascript", "-e", 'tell application "Ollama" to quit'],
                           timeout=10, check=False, capture_output=True)
        except Exception as exc:
            return f"could not quit the Ollama app: {type(exc).__name__}"
        return "asked the Ollama app to quit"
    return ("your Ollama was started outside Presence — stop it where you started it "
            "(the terminal running `ollama serve`, or the Ollama app)")


def overview(base_url: str | None = None) -> dict[str, Any]:
    """Everything the Models page shows, in one call: the engine, the machine,
    every model on this computer with its size and whether it is loaded."""
    st = status(base_url)
    url = st["base_url"]
    root = models_dir(st.get("engine"))
    if st["running"]:
        models = list_models(url)
        live = loaded_models(url)
        for m in models:
            m.update(show_model(m["name"], m["digest"], url))
            if m["name"] in live:
                m.update(live[m["name"]], loaded=True)
    else:
        models = models_on_disk(root)
    total = sum(m["size"] for m in models)
    return {**st, "models": models, "models_dir": str(root),
            "models_size_gb": round(total / 2**30, 2), "disk": disk_usage(root),
            "loaded_count": sum(1 for m in models if m["loaded"])}
