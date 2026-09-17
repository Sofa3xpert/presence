"""Read a CV properly: the person's own model fills a small resume schema,
section by section. Adapted from HackerRank's hiring-agent (MIT), trimmed to
what Presence needs: who the person is, what they can do, what they did.

The output feeds two things: the facts on the profile form (name, contact,
skills, links) and, later, the follow-up writer's evidence list (work and
project highlights). Nothing here is used until the person confirms it."""

from __future__ import annotations

import re
from typing import Any

from presence.core.structured import ask_json

MAX_CHARS = 12000  # about 3k tokens: fits a small model's default context with room to answer

SYSTEM = ("You extract facts from a CV into JSON. Copy facts exactly as written; never invent, "
          "never guess. Leave a field empty or the list empty when the CV does not say. "
          "Answer with JSON only.")

BASICS = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "location": {"type": "string"},
        "headline": {"type": "string", "description": "the person's own one-line title, if any"},
        "summary": {"type": "string"},
        "links": {"type": "array", "items": {"type": "string"},
                  "description": "URLs: GitHub, LinkedIn, portfolio, personal site"},
    },
    "required": ["name", "email", "phone", "location", "headline", "summary", "links"],
}
SKILLS = {
    "type": "object",
    "properties": {
        "skills": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"},
                           "keywords": {"type": "array", "items": {"type": "string"}}},
            "required": ["name", "keywords"]}},
        "languages": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["skills", "languages"],
}
WORK = {
    "type": "object",
    "properties": {"work": {"type": "array", "items": {
        "type": "object",
        "properties": {"company": {"type": "string"}, "position": {"type": "string"},
                       "start": {"type": "string"}, "end": {"type": "string"},
                       "highlights": {"type": "array", "items": {"type": "string"}}},
        "required": ["company", "position", "start", "end", "highlights"]}}},
    "required": ["work"],
}
EDUCATION = {
    "type": "object",
    "properties": {"education": {"type": "array", "items": {
        "type": "object",
        "properties": {"institution": {"type": "string"}, "degree": {"type": "string"},
                       "area": {"type": "string"}, "start": {"type": "string"},
                       "end": {"type": "string"}, "grade": {"type": "string"}},
        "required": ["institution", "degree", "area", "start", "end", "grade"]}}},
    "required": ["education"],
}
PROJECTS = {
    "type": "object",
    "properties": {"projects": {"type": "array", "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                       "url": {"type": "string"},
                       "highlights": {"type": "array", "items": {"type": "string"}}},
        "required": ["name", "description", "url", "highlights"]}}},
    "required": ["projects"],
}
SECTIONS: list[tuple[str, dict[str, Any], str]] = [
    ("basics", BASICS, "Who is this person? Name, email, phone, location, their own headline "
                       "and summary if present, and every URL."),
    ("skills", SKILLS, "List the person's skills. Group them the way the CV does (for example "
                       "'Programming' with keywords 'Python, SQL'); when the CV lists them "
                       "flat, use one group named 'Skills'. Include spoken languages "
                       "separately."),
    ("work", WORK, "List each job or placement: employer, position, start, end, and the "
                   "bullet points as written."),
    ("education", EDUCATION, "List each education entry: institution, degree, subject area, "
                             "dates, grade if stated."),
    ("projects", PROJECTS, "List each project: name, one-line description, URL if given, and "
                           "the bullet points as written."),
]


def extract(text: str, kind: str, model: str, *, base_url: str | None = None,
            api_key: str = "", sections: list[str] | None = None,
            ask=None) -> dict[str, Any]:
    """Run every section against the model and merge. Errors surface per section."""
    ask = ask or ask_json  # resolved at call time, so tests can swap the model out
    out: dict[str, Any] = {"errors": {}}
    for name, schema, instruction in SECTIONS:
        if sections and name not in sections:
            continue
        prompt = f"{instruction}\n\nCV:\n\"\"\"\n{text[:MAX_CHARS]}\n\"\"\""
        try:
            out[name] = ask(kind, model, SYSTEM, prompt, schema, base_url=base_url,
                            api_key=api_key)
        except Exception as exc:
            out["errors"][name] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return out


def _clean(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def to_fields(extracted: dict[str, Any]) -> dict[str, Any]:
    """The flat fields the profile form uses, from the sectioned extraction."""
    basics = extracted.get("basics") or {}
    skills: list[str] = []
    for group in (extracted.get("skills") or {}).get("skills") or []:
        for kw in [group.get("name", "")] + list(group.get("keywords") or []):
            kw = _clean(kw)
            if kw and kw.lower() not in {s.lower() for s in skills} and \
                    kw.lower() not in {"skills", "technical skills", "other"}:
                skills.append(kw)
    links = []
    for link in list(basics.get("links") or []):
        link = _clean(link).removeprefix("https://").removeprefix("http://").removeprefix("www.")
        if link and link not in links:
            links.append(link)
    return {
        "name": _clean(basics.get("name")),
        "email": _clean(basics.get("email")),
        "phone": _clean(basics.get("phone")),
        "skills": skills,
        "links": links,
        "headline": _clean(basics.get("headline")),
        "location": _clean(basics.get("location")),
    }


def facts(extracted: dict[str, Any]) -> list[dict[str, str]]:
    """Evidence lines for later use: each highlight with where it comes from."""
    out = []
    for w in (extracted.get("work") or {}).get("work") or []:
        where = " · ".join(x for x in (_clean(w.get("position")), _clean(w.get("company"))) if x)
        for h in w.get("highlights") or []:
            if _clean(h):
                out.append({"claim": _clean(h), "source": where or "work"})
    for p in (extracted.get("projects") or {}).get("projects") or []:
        for h in [p.get("description", "")] + list(p.get("highlights") or []):
            if _clean(h):
                out.append({"claim": _clean(h), "source": _clean(p.get("name")) or "project"})
    return out
