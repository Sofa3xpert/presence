"""Which model the person chose, in one place: kind, name, where, and the key.

Four kinds, decided from the provider entry itself and never from what its
address looks like:
  * "local"     — the provider named ``local`` pointing at the Ollama engine
                  (Presence's embedded copy or the person's own on 11434);
  * "endpoint"  — any other OpenAI-compatible server the person runs or rents
                  (vLLM, llama.cpp, LM Studio, ...): its address is used as given;
  * "anthropic" and "openai" — the two hosted APIs, under the person's key."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from presence.app import ollama, ollama_embedded
from presence.app.config_io import read_secrets, read_yaml

SECRET_NAMES = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "endpoint": "ENDPOINT_API_KEY",
    "nim": "NVIDIA_API_KEY",
}
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def _ollama_ports() -> set[int]:
    """Ports the Ollama engine answers on: the usual one, the embedded engine's
    live port, and the port it recorded last time (it may be starting up)."""
    ports = {ollama_embedded.SYSTEM_PORT}
    eng = ollama.engine()
    if eng is not None:
        try:
            ports.add(int(urlsplit(eng.base_url()).port or 0))
            if eng.portfile.exists():
                ports.add(int(eng.portfile.read_text().strip()))
        except (OSError, ValueError):
            pass
    ports.discard(0)
    return ports


NIM_HOSTED = "https://integrate.api.nvidia.com/v1"
NIM_LOCAL = "http://localhost:8000/v1"


def is_nim_hosted(base_url: str | None) -> bool:
    """NVIDIA's own endpoint, which needs a key; a NIM container you run does not."""
    return str(base_url or "").strip().rstrip("/").startswith(NIM_HOSTED)


def is_ollama_url(base_url: str | None) -> bool:
    """Is this address the Ollama engine on this computer?"""
    if not base_url:
        return True  # the local provider with no address means the engine
    parts = urlsplit(str(base_url).strip())
    if parts.hostname not in LOOPBACK:
        return False
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return port in _ollama_ports()


def derive_kind(pname: str, pcfg: dict[str, Any]) -> str:
    """The kind a provider entry means — see the module docstring."""
    if pname == "anthropic" or pcfg.get("kind") == "anthropic":
        return "anthropic"
    base_url = str(pcfg.get("base_url") or "").strip()
    if pname == "local":
        return "local" if is_ollama_url(base_url) else "endpoint"
    if pname == "openai" or not base_url:
        return "openai"
    return "endpoint"   # nim included: an address plus a key is an endpoint


def current(data: Path) -> dict[str, Any]:
    cfg = read_yaml(data / "presence.yaml")
    scout = (cfg.get("agents") or {}).get("scout", {})
    pname = scout.get("provider", "local")
    pcfg = (cfg.get("providers") or {}).get(pname, {})
    sec = read_secrets(data)
    kind = derive_kind(pname, pcfg)
    model = scout.get("model", "")
    if kind == "local":
        return {"kind": kind, "model": model, "base_url": ollama.resolve_base_url(),
                "api_key": ""}
    secret = pcfg.get("api_key_secret") or SECRET_NAMES[kind]
    if kind == "endpoint":  # the address exactly as the person gave it
        return {"kind": kind, "model": model, "base_url": str(pcfg.get("base_url")).strip(),
                "api_key": sec.get(secret, "") or "unused"}
    return {"kind": kind, "model": model,
            "base_url": (pcfg.get("base_url") or None) if kind == "openai" else None,
            "api_key": sec.get(secret, "")}


def budget(data: Path):
    """The day's token cap from presence.yaml, ledger in the data folder."""
    from presence.core.budget import DailyBudget

    cfg = read_yaml(data / "presence.yaml")
    cap = int(cfg.get("budget_tokens_per_day") or 200_000)
    return DailyBudget(cap, data / "budget.json")
