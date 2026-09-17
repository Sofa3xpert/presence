"""Importing an existing tracker — an 11-column sheet layout as the fixture."""

from presence.tracker import Tracker
from presence.tracker.importer import import_rows

HEADER = [
    "Tier",
    "Today",
    "Company",
    "Role",
    "Location",
    "Channel",
    "Status",
    "Applied",
    "Contact / Referral",
    "Next action",
    "Link",
]
ROWS = [
    [
        "A",
        "",
        "Acme",
        "Graduate AI Engineer",
        "London",
        "linkedin",
        "Rejected",
        "13 Aug → rejected 26 Aug",
        "",
        "",
        "https://x/1",
    ],
    [
        "B",
        "★",
        "Beta",
        "Data Analyst",
        "Remote",
        "board",
        "Applied",
        "2 Sep",
        "Sam (referral)",
        "chase 20 Sep",
        "https://x/2",
    ],
    [
        "A",
        "",
        "Acme",
        "Grad AI Engineer",
        "London",
        "board",
        "Applied",
        "13 Aug",
        "",
        "",
        "https://x/1",
    ],  # same link → duplicate
    ["", "", "", "", "", "", "", "", "", "", ""],
    ["C", "", "Gamma", "SWE", "", "", "Ghosted", "", "", "", ""],
    ["C", "", "Delta", "Analyst", "", "", "Ignored (Under-qualified)", "", "", "", ""],
    ["C", "", "Epsilon", "ML Eng", "", "", "Outdated", "", "", "", ""],
]


def test_import_maps_columns_and_conventions(tmp_path):
    t = Tracker(tmp_path / "t.db")
    rep = import_rows(t, HEADER, ROWS, year=2026)
    assert (rep.created, rep.duplicates, rep.skipped) == (5, 1, 1)
    by = {j.company: j for j in t.list()}
    acme = by["Acme"]
    assert acme.status == "rejected" and t.timeline(acme.id) == "13 Aug → rejected 26 Aug"
    assert acme.source == "linkedin" and acme.tier == "A" and acme.url == "https://x/1"
    beta = by["Beta"]
    assert beta.status == "applied" and t.timeline(beta.id) == "2 Sep"
    assert beta.extra == {
        "Today": "★",
        "Contact / Referral": "Sam (referral)",
        "next_action": "chase 20 Sep",
    }
    assert by["Gamma"].status == "to_apply" and "unknown status 'Ghosted'" in rep.issues[0]
    assert by["Delta"].status == "ignored" and by["Delta"].note == "Under-qualified"
    assert by["Epsilon"].status == "ignored"
    assert "5 added, 1 already tracked, 1 skipped, 1 issue(s)" == rep.summary()
    assert import_rows(t, HEADER, ROWS, year=2026).created == 0  # a second run adds nothing
