"""The source catalog the app shows: what Presence can read, on what terms.

`available` entries have a connector. `planned` entries are next. `on_hold`
entries are offered interfaces whose terms are still under review — listed
so the choice is visible, never enabled. Nothing scraped is ever listed."""

from __future__ import annotations

CATALOG: list[dict[str, str | bool]] = [
    {
        "provider": "greenhouse",
        "name": "Greenhouse Job Board API",
        "kind": "board",
        "status": "available",
        "needs_key": False,
        "docs": "https://developers.greenhouse.io/job-board.html",
        "note": "one company per source; add the board token from the company's careers page",
    },
    {
        "provider": "lever",
        "name": "Lever Postings API",
        "kind": "board",
        "status": "available",
        "needs_key": False,
        "docs": "https://github.com/lever/postings-api",
        "note": "one company per source",
    },
    {
        "provider": "ashby",
        "name": "Ashby Job Posting API",
        "kind": "board",
        "status": "available",
        "needs_key": False,
        "docs": "https://developers.ashbyhq.com/reference/jobpostingapi",
        "note": "one company per source",
    },
    {
        "provider": "workable",
        "name": "Workable job widget API",
        "kind": "board",
        "status": "available",
        "needs_key": False,
        "docs": "https://workable.readme.io/",
        "note": "one company per source",
    },
    {
        "provider": "smartrecruiters",
        "name": "SmartRecruiters Posting API",
        "kind": "board",
        "status": "available",
        "needs_key": False,
        "docs": "https://developers.smartrecruiters.com/docs/posting-api",
        "note": "one company per source",
    },
    {
        "provider": "email_alerts",
        "name": "Your own job-alert emails",
        "kind": "inbox",
        "status": "planned",
        "needs_key": False,
        "docs": "",
        "note": "LinkedIn / Indeed / StudySmarter alerts you already receive, "
        "read-only from your mailbox",
    },
    {
        "provider": "adzuna",
        "name": "Adzuna Jobs API",
        "kind": "aggregator",
        "status": "on_hold",
        "needs_key": True,
        "docs": "https://developer.adzuna.com/",
        "note": "terms under review",
    },
    {
        "provider": "reed",
        "name": "Reed.co.uk Jobs API",
        "kind": "aggregator",
        "status": "on_hold",
        "needs_key": True,
        "docs": "https://www.reed.co.uk/developers",
        "note": "terms under review",
    },
    {
        "provider": "jooble",
        "name": "Jooble API",
        "kind": "aggregator",
        "status": "on_hold",
        "needs_key": True,
        "docs": "https://jooble.org/api/about",
        "note": "terms under review",
    },
    {
        "provider": "careerjet",
        "name": "Careerjet API",
        "kind": "aggregator",
        "status": "on_hold",
        "needs_key": False,
        "docs": "https://www.careerjet.com/partners/api/",
        "note": "terms under review",
    },
]


def available() -> list[dict[str, str | bool]]:
    return [c for c in CATALOG if c["status"] == "available"]
