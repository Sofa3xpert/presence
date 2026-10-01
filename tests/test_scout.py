"""Tests for the Opportunity Scout agent and RSS connector."""

import xml.etree.ElementTree as ET
from datetime import date

from presence.agents.scout import (
    Opportunity,
    ScoutConfig,
    SeenOpportunities,
    compose_scout_digest,
    matches_scout,
)
from presence.connectors.rss import (
    _guess_kind,
    _parse_atom_entries,
    _parse_date,
    _parse_rss_items,
)

# --- Opportunity model ---

def test_opportunity_key_from_url():
    opp = Opportunity(title="Intern", url="https://example.com/jobs/123/")
    assert opp.key == "https://example.com/jobs/123"


def test_opportunity_key_from_title():
    opp = Opportunity(title="Summer Intern", organization="Google")
    assert opp.key == "google|summer intern"


# --- Matching ---

def test_matches_default_keywords():
    config = ScoutConfig()  # default keywords: intern, graduate, student, trainee
    opp = Opportunity(title="Software Engineering Intern", organization="Google")
    assert matches_scout(opp, config)


def test_no_match_without_keyword():
    config = ScoutConfig(keywords=["intern"])
    opp = Opportunity(title="Senior Manager", organization="Google")
    assert not matches_scout(opp, config)


def test_exclude_keyword():
    config = ScoutConfig(keywords=["intern"], keywords_exclude=["marketing"])
    opp = Opportunity(title="Marketing Intern", organization="Google")
    assert not matches_scout(opp, config)


def test_location_filter():
    config = ScoutConfig(keywords=["intern"], locations=["Hong Kong"])
    match = Opportunity(title="Intern", location="Hong Kong")
    no_match = Opportunity(title="Intern", location="London")
    assert matches_scout(match, config)
    assert not matches_scout(no_match, config)


def test_location_in_description():
    config = ScoutConfig(keywords=["intern"], locations=["Remote"])
    opp = Opportunity(title="Intern", location="", description="This is a remote position")
    assert matches_scout(opp, config)


def test_kind_filter():
    config = ScoutConfig(keywords=["intern"], kinds=["internship", "scholarship"])
    opp = Opportunity(title="Intern event", kind="event")
    assert not matches_scout(opp, config)


def test_empty_keywords_matches_all():
    config = ScoutConfig(keywords=[])
    opp = Opportunity(title="Anything at all")
    assert matches_scout(opp, config)


# --- SeenOpportunities ---

def test_seen_opportunities(tmp_path):
    path = tmp_path / "seen.json"
    seen = SeenOpportunities(path)
    assert not seen.is_seen("key1")
    seen.mark(["key1", "key2"])
    assert seen.is_seen("key1")
    assert seen.is_seen("key2")
    # reload from disk
    seen2 = SeenOpportunities(path)
    assert seen2.is_seen("key1")


# --- Compose digest ---

def test_digest_empty():
    digest = compose_scout_digest([], today=date(2026, 9, 29))
    assert "No new opportunities" in digest
    assert "Tue 29 Sep" in digest


def test_digest_with_opportunities():
    opps = [
        Opportunity(title="SWE Intern", organization="Google", kind="internship",
                    url="https://google.com/intern"),
        Opportunity(title="HK Scholarship", kind="scholarship",
                    deadline="2026-10-15"),
        Opportunity(title="Career Fair", kind="event", location="PolyU"),
    ]
    digest = compose_scout_digest(opps, today=date(2026, 9, 29))
    assert "3 new opportunities" in digest
    assert "Internships (1):" in digest
    assert "Google: SWE Intern" in digest
    assert "Scholarships (1):" in digest
    assert "(due in 16 days)" in digest
    assert "Events & Career Fairs (1):" in digest
    assert "Good luck!" in digest


def test_digest_deadline_today():
    opps = [Opportunity(title="Due Now", kind="scholarship", deadline="2026-09-29")]
    digest = compose_scout_digest(opps, today=date(2026, 9, 29))
    assert "(due TODAY)" in digest


# --- RSS connector ---


def test_guess_kind():
    assert _guess_kind("Summer Internship", "") == "internship"
    assert _guess_kind("Merit Scholarship", "") == "scholarship"
    assert _guess_kind("Career Fair 2026", "") == "event"
    assert _guess_kind("Hackathon", "") == "event"
    assert _guess_kind("Mentorship Program", "") == "mentorship"
    assert _guess_kind("Something Else", "") == "other"


def test_parse_date_rfc2822():
    assert _parse_date("Mon, 29 Sep 2026 08:00:00 GMT") == "2026-09-29"


def test_parse_date_iso():
    assert _parse_date("2026-09-29T08:00:00Z") == "2026-09-29"
    assert _parse_date("2026-09-29") == "2026-09-29"


def test_parse_date_empty():
    assert _parse_date("") == ""
    assert _parse_date("not a date") == ""


def test_parse_rss_items():
    xml_str = """<rss><channel>
    <item>
      <title>Google Summer Internship</title>
      <link>https://google.com/intern</link>
      <description>A great opportunity for students</description>
      <pubDate>Mon, 29 Sep 2026 08:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Scholarship Award</title>
      <link>https://example.com/scholarship</link>
      <description>Apply for this scholarship</description>
    </item>
    </channel></rss>"""
    root = ET.fromstring(xml_str)
    opps = _parse_rss_items(root, "https://example.com/feed", "")
    assert len(opps) == 2
    assert opps[0].title == "Google Summer Internship"
    assert opps[0].kind == "internship"
    assert opps[1].title == "Scholarship Award"
    assert opps[1].kind == "scholarship"


def test_parse_atom_entries():
    xml_str = """<feed xmlns="http://www.w3.org/2005/Atom">
    <entry>
      <title>Hackathon 2026</title>
      <link href="https://example.com/hackathon"/>
      <summary>Join our annual hackathon</summary>
      <updated>2026-10-01T00:00:00Z</updated>
    </entry>
    </feed>"""
    root = ET.fromstring(xml_str)
    opps = _parse_atom_entries(root, "https://example.com/feed", "")
    assert len(opps) == 1
    assert opps[0].title == "Hackathon 2026"
    assert opps[0].kind == "event"
    assert opps[0].url == "https://example.com/hackathon"


def test_parse_rss_with_kind_override():
    xml_str = """<rss><channel>
    <item><title>Random Title</title><link>https://example.com</link></item>
    </channel></rss>"""
    root = ET.fromstring(xml_str)
    opps = _parse_rss_items(root, "https://example.com/feed", "scholarship")
    assert opps[0].kind == "scholarship"
