"""RSS/Atom feed connector — reads any RSS or Atom feed and returns Opportunities.

Uses Python's built-in xml.etree.ElementTree (no extra dependencies).
Works with scholarship databases, university career pages, event boards,
and any site that publishes an RSS or Atom feed.

How RSS works (для понимания):
  A website publishes a special XML file at a URL (like a news feed).
  Each <item> in the feed is one entry (an article, event, opportunity).
  We read that XML, pull out the title/link/description, and turn each
  item into an Opportunity.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from presence.agents.scout import Opportunity

log = logging.getLogger("presence.rss")

HEADERS = {
    "User-Agent": "presence/0.0.2 (+student-scout)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
}
TIMEOUT = 20

# Atom uses a namespace — we need this to find elements
ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _get_xml(url: str) -> ET.Element:
    """Fetch a URL and parse the response as XML."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return ET.fromstring(r.content)
    except requests.RequestException as exc:
        log.warning("Failed to fetch RSS feed %s: %s", url, exc)
        raise
    except ET.ParseError as exc:
        log.warning("Failed to parse XML from %s: %s", url, exc)
        raise


def _text(el: ET.Element | None) -> str:
    """Safely get text from an XML element."""
    if el is None:
        return ""
    return (el.text or "").strip()


def _parse_date(raw: str) -> str:
    """Try to parse an RSS/Atom date into ISO format. Returns '' on failure."""
    if not raw:
        return ""
    # RSS uses RFC 2822 dates: "Mon, 29 Sep 2026 08:00:00 GMT"
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except Exception:
        pass
    # Atom uses ISO 8601: "2026-09-29T08:00:00Z"
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _guess_kind(title: str, description: str) -> str:
    """Guess the opportunity kind from the title and description text."""
    text = f"{title} {description}".lower()
    if any(w in text for w in ("intern", "trainee", "apprentice", "co-op", "coop")):
        return "internship"
    if any(w in text for w in ("scholarship", "fellowship", "grant", "bursary", "award")):
        return "scholarship"
    if any(w in text for w in ("event", "fair", "hackathon", "workshop", "conference",
                                "meetup", "webinar", "seminar", "networking")):
        return "event"
    if any(w in text for w in ("mentor", "mentorship", "coaching")):
        return "mentorship"
    return "other"


def _parse_rss_items(root: ET.Element, feed_url: str, kind_override: str) -> list[Opportunity]:
    """Parse RSS 2.0 <item> elements."""
    items = root.findall(".//item")
    results: list[Opportunity] = []
    for item in items:
        title = _text(item.find("title"))
        if not title:
            continue
        link = _text(item.find("link"))
        desc = _text(item.find("description"))
        pub_date = _text(item.find("pubDate"))

        kind = kind_override or _guess_kind(title, desc)
        results.append(Opportunity(
            title=title,
            url=link,
            description=desc[:500],  # truncate long descriptions
            kind=kind,
            source=f"rss:{feed_url[:60]}",
            found_date=date.today().isoformat(),
            deadline=_parse_date(pub_date),  # RSS pubDate ≠ deadline, but it's what we have
        ))
    return results


def _parse_atom_entries(root: ET.Element, feed_url: str, kind_override: str) -> list[Opportunity]:
    """Parse Atom <entry> elements."""
    entries = root.findall(f"{ATOM_NS}entry")
    if not entries:
        # try without namespace (some feeds are sloppy)
        entries = root.findall("entry")
    results: list[Opportunity] = []
    for entry in entries:
        title = _text(entry.find(f"{ATOM_NS}title")) or _text(entry.find("title"))
        if not title:
            continue
        # Atom links are in <link href="..."/> attributes
        link_el = entry.find(f"{ATOM_NS}link")
        if link_el is None:
            link_el = entry.find("link")
        link = (link_el.get("href", "") if link_el is not None else "")
        # description can be in <summary> or <content>
        desc = (_text(entry.find(f"{ATOM_NS}summary"))
                or _text(entry.find(f"{ATOM_NS}content"))
                or _text(entry.find("summary"))
                or _text(entry.find("content")))
        updated = (_text(entry.find(f"{ATOM_NS}updated"))
                   or _text(entry.find(f"{ATOM_NS}published"))
                   or _text(entry.find("updated")))

        kind = kind_override or _guess_kind(title, desc)
        results.append(Opportunity(
            title=title,
            url=link,
            description=desc[:500],
            kind=kind,
            source=f"rss:{feed_url[:60]}",
            found_date=date.today().isoformat(),
            deadline=_parse_date(updated),
        ))
    return results


def fetch_rss_feed(
    url: str,
    kind: str = "",
    label: str = "",
) -> list[Opportunity]:
    """Fetch one RSS or Atom feed and return Opportunities.

    Args:
        url: The feed URL.
        kind: Force all items to this kind (e.g. "scholarship"). If empty,
              the kind is guessed from the title/description.
        label: Human-readable label for this feed (for logging).
    """
    log.info("Fetching RSS feed: %s (%s)", label or url, kind or "auto-detect")
    try:
        root = _get_xml(url)
    except Exception:
        return []

    # Detect format: RSS has <rss> or <channel>, Atom has <feed>
    tag = root.tag.lower().split("}")[-1]  # strip namespace if present

    if tag == "rss" or root.find("channel") is not None:
        return _parse_rss_items(root, url, kind)
    elif tag == "feed":
        return _parse_atom_entries(root, url, kind)
    else:
        log.warning("Unknown feed format (root tag: %s) for %s", root.tag, url)
        return []


def fetch_all_feeds(feeds: list[dict[str, str]]) -> list[Opportunity]:
    """Fetch multiple RSS feeds and combine results.

    Each feed dict has: {url: ..., label: ..., kind: ...}
    """
    all_opps: list[Opportunity] = []
    for feed in feeds:
        url = feed.get("url", "").strip()
        if not url:
            continue
        opps = fetch_rss_feed(
            url=url,
            kind=feed.get("kind", ""),
            label=feed.get("label", ""),
        )
        all_opps.extend(opps)
    return all_opps
