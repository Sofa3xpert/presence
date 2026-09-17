from datetime import date

from presence.tracker.conventions import (
    link_key,
    norm_company,
    parse_timeline,
    render_timeline,
    title_similarity,
)


def test_timeline_renders_house_style():
    ev = [("applied", date(2026, 8, 29), ""), ("assessment", date(2026, 9, 1), "CCAT"),
          ("rejected", date(2026, 9, 8), ""), ("note", date(2026, 9, 8), "ignored in timeline")]
    assert render_timeline(ev) == "29 Aug → CCAT 1 Sep → rejected 8 Sep"
    assert render_timeline([("applied", date(2026, 8, 16), "")]) == "16 Aug"
    assert render_timeline([("rejected", date(2026, 8, 22), "")]) == "rejected 22 Aug"
    assert render_timeline([]) == ""


def test_timeline_parse_roundtrip():
    text = "29 Aug → CCAT 1 Sep → rejected 8 Sep"
    ev = parse_timeline(text, 2026)
    assert [(k, d.isoformat(), x) for k, d, x in ev] == [
        ("applied", "2026-08-29", ""), ("assessment", "2026-09-01", "CCAT"),
        ("rejected", "2026-09-08", ""),
    ]
    assert render_timeline(ev) == text
    assert parse_timeline("29 Aug (re-applied)", 2026)[0][0] == "applied"
    assert parse_timeline("Yes", 2026) == []
    assert parse_timeline("17 Aug → interview 22 Aug", 2026)[1][0] == "interview"


def test_link_keys_dedupe_boards_and_tracking_params():
    assert link_key("https://www.linkedin.com/jobs/view/4450895458?refId=x") == "li:4450895458"
    assert link_key("https://uk.indeed.com/viewjob?jk=d27ac3830c7cb0a3&from=alert") == \
        "indeed:d27ac3830c7cb0a3"
    assert link_key("https://www.amazon.jobs/en/jobs/10403067/ai-automation-sde") == "id:10403067"
    assert link_key("https://jobs.lever.co/palantir/2aa14e4f-d406/") == \
        "url:https://jobs.lever.co/palantir/2aa14e4f-d406"
    assert link_key("") == ""


def test_normalisers():
    assert norm_company("Palantir Technologies") == norm_company("Palantir")
    assert norm_company("Accenture UK & Ireland") == "accenture"
    assert title_similarity("Graduate AI Engineer", "AI Engineer") == 1.0
    assert title_similarity("Data Scientist", "Quantitative Developer") < 0.5


def test_messaged_is_neutral_and_round_trips():
    from datetime import date

    from presence.tracker.conventions import (
        EVENT_KINDS,
        EVENT_STATUS,
        parse_timeline,
        render_timeline,
    )

    assert "messaged" in EVENT_KINDS and "messaged" not in EVENT_STATUS
    events = [("applied", date(2026, 8, 13), ""), ("messaged", date(2026, 8, 23), ""),
              ("rejected", date(2026, 8, 26), "")]
    text = render_timeline(events)
    assert text == "13 Aug → messaged 23 Aug → rejected 26 Aug"
    assert [k for k, _, _ in parse_timeline(text, 2026)] == ["applied", "messaged", "rejected"]
