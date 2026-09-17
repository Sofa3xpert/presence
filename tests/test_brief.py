"""The daily brief: portable date line, counts, closing sentence."""

from datetime import date

from presence.agents.brief import compose_brief
from presence.tracker import Candidate, Tracker


def test_date_line_has_no_leading_zero_and_no_platform_flags(tmp_path):
    t = Tracker(tmp_path / "t.db")
    try:
        job, _ = t.ingest(Candidate(company="Acme", title="Analyst", url="https://x/1",
                                    source="greenhouse"))
        text = compose_brief(t, [job], {}, today=date(2026, 9, 3))
    finally:
        t.close()
    assert text.startswith("Presence · Thu 3 Sep")
    assert "%" not in text.splitlines()[0]
    assert "1 new role worth a look" in text and "Nothing was sent on your behalf." in text
