"""Configuration: the customer's profile, search parameters and runtime
settings — YAML files validated by schema, with errors a person can act on.

Three files live in the data directory:
    profile.yaml   confirmed facts about the customer (charter rule 2 lives here)
    sources.yaml   which boards and companies to read (published APIs only)
    search.yaml    the customer's filters — locations, title rules, blocklist
    presence.yaml  runtime: providers, agents, budget
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError


class ConfigError(Exception):
    """A configuration problem, worded for the person who has to fix it."""


class Identity(BaseModel):
    name: str
    email: str | None = None


class WorkAuthorization(BaseModel):
    summary: str  # e.g. "UK Graduate visa — full right to work, no sponsorship needed"
    needs_sponsorship: bool | None = None


class Profile(BaseModel):
    identity: Identity
    work_authorization: WorkAuthorization
    skills: list[str] = Field(default_factory=list)
    # Charter rule 2: extracted facts are never acted on until the customer
    # confirms them. The runtime refuses to run agents while this is False.
    confirmed: bool = False


class SearchConfig(BaseModel):
    """The customer's filters, applied to postings from every source."""

    locations: list[str] = Field(default_factory=list)  # empty = anywhere
    remote_ok: bool = True
    max_experience_years: int = 2
    title_include: list[str] = Field(default_factory=list)  # empty = keep all titles
    title_exclude: list[str] = Field(default_factory=lambda: [
        "senior", "staff", "principal", "lead", "manager", "director", "head of", "vp"])
    blocklist: list[str] = Field(default_factory=list)  # companies never shown
    freshness_hours: int = 24 * 14  # boards list long-open roles; two weeks by default


class SourceEntry(BaseModel):
    """One enabled source: a company's board on a published API (or, later, a mailbox)."""

    id: str
    provider: str
    label: str = ""
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class SourcesConfig(BaseModel):
    sources: list[SourceEntry] = Field(default_factory=list)


class ProviderConfig(BaseModel):
    kind: Literal["anthropic", "openai-compatible"]
    api_key_secret: str | None = None  # secret name looked up via core.secrets
    base_url: str | None = None  # for openai-compatible local servers (Ollama, LM Studio)


class AgentRuntimeConfig(BaseModel):
    provider: str  # key into AppConfig.providers
    model: str
    at: str = "08:00"  # daily slot, HH:MM
    max_turns: int = 12
    max_output_tokens: int = 1024


class AppConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    agents: dict[str, AgentRuntimeConfig]
    budget_tokens_per_day: int = 200_000


def load_yaml_model[M: BaseModel](path: Path, model: type[M]) -> M:
    """Load and validate one YAML file, translating failures into messages
    that name the file, the field and what to do."""
    if not path.exists():
        raise ConfigError(f"{path.name} not found in {path.parent} — create it or rerun onboarding")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc
    if raw is None:
        raise ConfigError(f"{path.name} is empty — create it or rerun onboarding")
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '(top level)'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ConfigError(f"{path.name}: {problems}") from exc


def load_profile(data_dir: Path) -> Profile:
    profile = load_yaml_model(data_dir / "profile.yaml", Profile)
    if not profile.confirmed:
        raise ConfigError(
            "profile.yaml has confirmed: false — Presence acts only on facts the "
            "customer has confirmed. Review the profile and set confirmed: true."
        )
    return profile


def load_search(data_dir: Path) -> SearchConfig:
    return load_yaml_model(data_dir / "search.yaml", SearchConfig)


def load_sources(data_dir: Path) -> list[SourceEntry]:
    return load_yaml_model(data_dir / "sources.yaml", SourcesConfig).sources


def load_app(data_dir: Path) -> AppConfig:
    config = load_yaml_model(data_dir / "presence.yaml", AppConfig)
    for name, agent in config.agents.items():
        if agent.provider not in config.providers:
            raise ConfigError(
                f"presence.yaml: agent '{name}' uses provider '{agent.provider}' "
                f"but providers defines only: {', '.join(config.providers) or '(none)'}"
            )
    return config
