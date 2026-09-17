"""Turn a careers URL into a source: detect the ATS and its board token."""

from __future__ import annotations

import re

PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/(?:embed/job_board\?for=)([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([\w-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([\w-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", re.I)),
]


def detect(url: str) -> tuple[str, str] | None:
    for provider, pat in PATTERNS:
        m = pat.search(url or "")
        if m:
            return provider, m.group(1)
    return None
