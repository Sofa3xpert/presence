"""Brief v0 — a rule-based daily digest. The judged, tiered version (W5)
replaces the ranking; the shape and the closing line stay."""

from __future__ import annotations

from datetime import date

from presence.tracker import OPEN_STATUSES, Job, Tracker


def compose_brief(tracker: Tracker, new_jobs: list[Job], errors: dict[str, str],
                  filtered_out: int = 0, today: date | None = None, limit: int = 10) -> str:
    today = today or date.today()
    # day without a leading zero, spelled by hand: strftime's %-d is not portable
    lines = [f"Presence · {today:%a} {today.day} {today:%b}"]
    if new_jobs:
        lines.append(f"{len(new_jobs)} new role{'s' if len(new_jobs) != 1 else ''} worth a look"
                     + (f" ({filtered_out} filtered out)" if filtered_out else "") + ":")
        for i, j in enumerate(new_jobs[:limit], 1):
            loc = f" — {j.location}" if j.location else ""
            lines.append(f"{i}. {j.company}: {j.title}{loc}\n   {j.url}")
        if len(new_jobs) > limit:
            lines.append(f"…and {len(new_jobs) - limit} more in the tracker.")
    else:
        lines.append("No new roles today" + (f" ({filtered_out} seen, none passed your filters)"
                                             if filtered_out else "") + ".")
    counts = {s: len(tracker.list(s)) for s in OPEN_STATUSES}
    open_total = sum(counts.values())
    lines.append(f"Tracker: {counts['to_apply']} to apply · {counts['applied']} applied · "
                 f"{counts['assessment'] + counts['interview']} in process · {open_total} open.")
    if errors:
        lines.append("Sources that failed: " + ", ".join(errors) + ".")
    lines.append("Nothing was sent on your behalf.")
    return "\n".join(lines)
