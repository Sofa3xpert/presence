"""LinkedIn connector — library-based search via python-jobspy.

Runs on the customer's machine as an ordinary client, one polite query at a
time, never with evasion of any kind (charter rule 1). A saved-search
alert-email connector is the planned complement; see base.py."""

from __future__ import annotations

from typing import Any

from presence.connectors.base import ConnectorError, Posting, SearchQuery, register


def _rows_to_postings(rows: list[dict[str, Any]], with_description: bool) -> list[Posting]:
    out = []
    for r in rows:
        title, company = str(r.get("title") or "").strip(), str(r.get("company") or "").strip()
        if not title or not company:
            continue
        desc = r.get("description") if with_description else ""
        out.append(Posting(
            company=company, title=title,
            location=str(r.get("location") or "").strip(),
            url=str(r.get("job_url") or "").strip(),
            source="linkedin",
            posted=str(r.get("date_posted") or "")[:10].replace("nan", ""),
            description=" ".join(str(desc).split())[:600] if isinstance(desc, str) else "",
        ))
    return out


@register("linkedin")
class LinkedInConnector:
    name = "linkedin"

    def __init__(self, fetch_description: bool = False):
        self.fetch_description = fetch_description

    def _scrape(self, query: SearchQuery) -> list[dict[str, Any]]:
        from jobspy import scrape_jobs  # lazy: heavy import, only when used

        df = scrape_jobs(
            site_name=["linkedin"],
            search_term=query.term,
            location=query.location or None,
            results_wanted=query.limit,
            hours_old=query.hours_old,
            linkedin_fetch_description=self.fetch_description,
        )
        return df.to_dict("records")

    def search(self, query: SearchQuery) -> list[Posting]:
        try:
            rows = self._scrape(query)
        except Exception as exc:  # jobspy raises a zoo of exceptions; isolate per query
            raise ConnectorError(f"linkedin: {type(exc).__name__}: {str(exc)[:160]}") from exc
        return _rows_to_postings(rows, self.fetch_description)
