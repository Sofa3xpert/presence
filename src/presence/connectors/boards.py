"""Published job-board APIs — the v1 sources.

Each provider documents this endpoint for exactly this use (embedding or
reading a company's open roles). No keys, no scraping: one GET per company,
and the job's own text comes with the listing where the API offers it.
SmartRecruiters lists jobs without their text, so that one is fetched per
posting — only once the tracker has created the job, never for every listing.
A SourceEntry names the provider and the company's board token, e.g.
    {id: figma, provider: greenhouse, label: Figma, config: {board: figma}}
"""

from __future__ import annotations

from typing import Any

import requests

from presence.connectors.base import ConnectorError, Posting, _iso, register
from presence.connectors.html import to_text
from presence.core.config import SourceEntry

HEADERS = {
    "User-Agent": "presence/0.0.1 (+https://github.com/Sofa3xpert/presence)",
    "Accept": "application/json",
}
TIMEOUT = 25


def _get(url: str, **kw: Any) -> Any:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kw)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # network, HTTP, JSON — all isolated per source
        raise ConnectorError(f"{type(exc).__name__}: {str(exc)[:140]}") from exc


def _board(src: SourceEntry) -> str:
    token = str(src.config.get("board", "")).strip()
    if not token:
        raise ConnectorError(f"source '{src.id}' has no config.board token")
    return token


def _sections(*parts: tuple[str, str]) -> str:
    """Plain text from (heading, html) pairs: a heading only above a non-empty body."""
    out = []
    for heading, body in parts:
        text = to_text(body)
        if text:
            head = to_text(heading)
            out.append(f"{head}\n{text}" if head else text)
    return "\n\n".join(out)


@register("greenhouse")
class Greenhouse:
    provider = "greenhouse"

    def fetch(self, src: SourceEntry) -> list[Posting]:
        data = _get(f"https://boards-api.greenhouse.io/v1/boards/{_board(src)}/jobs",
                    params={"content": "true"})
        return [
            Posting(
                company=src.label,
                title=j.get("title", ""),
                location=(j.get("location") or {}).get("name", ""),
                url=j.get("absolute_url", ""),
                source=self.provider,
                posted=_iso(j.get("updated_at")),
                description=to_text(j.get("content") or ""),
            )
            for j in data.get("jobs", [])
        ]


@register("lever")
class Lever:
    provider = "lever"

    def fetch(self, src: SourceEntry) -> list[Posting]:
        data = _get(f"https://api.lever.co/v0/postings/{_board(src)}", params={"mode": "json"})
        out = []
        for j in data:
            cats = j.get("categories") or {}
            loc = " / ".join(
                filter(None, [cats.get("location", "")] + (j.get("allLocations") or []))
            )
            out.append(
                Posting(
                    company=src.label,
                    title=j.get("text", ""),
                    location=loc,
                    url=j.get("hostedUrl", ""),
                    source=self.provider,
                    posted=_iso(j.get("createdAt")),
                    description=_sections(
                        ("", j.get("descriptionPlain") or j.get("description") or ""),
                        *[(lst.get("text", ""), lst.get("content", ""))
                          for lst in j.get("lists") or []],
                        ("", j.get("additionalPlain") or j.get("additional") or ""),
                    ),
                )
            )
        return out


@register("ashby")
class Ashby:
    provider = "ashby"

    def fetch(self, src: SourceEntry) -> list[Posting]:
        data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{_board(src)}")
        out = []
        for j in data.get("jobs", []):
            secondary = [s.get("location", "") for s in j.get("secondaryLocations") or []]
            loc = " / ".join(filter(None, [j.get("location", "")] + secondary))
            out.append(
                Posting(
                    company=src.label,
                    title=j.get("title", ""),
                    location=loc,
                    url=j.get("jobUrl", "") or j.get("applyUrl", ""),
                    source=self.provider,
                    posted=_iso(j.get("publishedAt")),
                    description=to_text(j.get("descriptionPlain") or j.get("descriptionHtml")
                                        or ""),
                )
            )
        return out


@register("workable")
class Workable:
    provider = "workable"

    def fetch(self, src: SourceEntry) -> list[Posting]:
        data = _get(
            f"https://apply.workable.com/api/v1/widget/accounts/{_board(src)}",
            params={"details": "true"},
        )
        return [
            Posting(
                company=src.label,
                title=j.get("title", ""),
                location=", ".join(filter(None, [j.get("city", ""), j.get("country", "")])),
                url=j.get("url", ""),
                source=self.provider,
                posted=_iso(j.get("published_on") or j.get("created_at")),
                description=_sections(
                    ("", j.get("description") or ""),
                    ("Requirements", j.get("requirements") or ""),
                    ("Benefits", j.get("benefits") or ""),
                ),
            )
            for j in data.get("jobs", [])
        ]


@register("smartrecruiters")
class SmartRecruiters:
    provider = "smartrecruiters"

    def fetch(self, src: SourceEntry) -> list[Posting]:
        board, out, offset = _board(src), [], 0
        while True:
            data = _get(
                f"https://api.smartrecruiters.com/v1/companies/{board}/postings",
                params={"limit": 100, "offset": offset},
            )
            for j in data.get("content", []):
                loc = j.get("location") or {}
                out.append(
                    Posting(
                        company=src.label,
                        title=j.get("name", ""),
                        location=", ".join(
                            filter(None, [loc.get("city", ""), loc.get("country", "")])
                        ),
                        url=f"https://jobs.smartrecruiters.com/{board}/{j.get('id', '')}",
                        source=self.provider,
                        posted=_iso(j.get("releasedDate")),
                        description_ref=str(j.get("id") or ""),
                    )
                )
            offset += 100
            if offset >= int(data.get("totalFound", 0)) or offset >= 500:
                break
        return out

    def fetch_description(self, src: SourceEntry, posting_id: str) -> str:
        """One posting's text — the list endpoint carries none. Called for a
        job the tracker has just created, one GET each."""
        data = _get(
            f"https://api.smartrecruiters.com/v1/companies/{_board(src)}/postings/{posting_id}"
        )
        sections = (data.get("jobAd") or {}).get("sections") or {}
        return _sections(*[
            ((sections.get(key) or {}).get("title", ""), (sections.get(key) or {}).get("text", ""))
            for key in ("jobDescription", "qualifications", "additionalInformation")
        ])
