"""Schedule upload — .ics file, calendar URL, or PDF timetable.

Handles three ways a student can add their schedule:
1. Upload a .ics file directly
2. Paste a calendar URL (fetches and saves as .ics)
3. Upload a PDF timetable (extracts text, LLM parses into events, generates .ics)
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from presence.connectors.ical import parse_ics


class ScheduleEvent(BaseModel):
    """A parsed schedule event from LLM output."""
    summary: str
    date: str          # YYYY-MM-DD
    start_time: str    # HH:MM
    end_time: str      # HH:MM
    location: str = ""
    recurrence: str = ""  # e.g. "WEEKLY;COUNT=15"


class UploadResult(BaseModel):
    ok: bool
    message: str
    filename: str = ""


def _validate_ics(text: str) -> bool:
    """Check that text looks like valid iCal with at least one VEVENT."""
    return "BEGIN:VCALENDAR" in text and "BEGIN:VEVENT" in text


def _save_ics(cal_dir: Path, text: str, filename: str) -> Path:
    """Save .ics content to the calendars directory."""
    cal_dir.mkdir(parents=True, exist_ok=True)
    dest = cal_dir / filename
    # avoid overwriting: add suffix if name taken
    if dest.exists():
        stem = dest.stem
        dest = cal_dir / f"{stem}_{uuid.uuid4().hex[:6]}.ics"
    dest.write_text(text, encoding="utf-8")
    return dest


def upload_ics_file(file_bytes: bytes, filename: str, cal_dir: Path) -> UploadResult:
    """Handle a .ics file upload."""
    try:
        text = file_bytes.decode("utf-8", errors="replace")
    except Exception:
        return UploadResult(ok=False, message="Could not read the file as text.")

    if not _validate_ics(text):
        return UploadResult(ok=False,
                            message="That file doesn't look like a valid .ics calendar "
                                    "(no VCALENDAR/VEVENT blocks found).")

    events = parse_ics(text)
    if not events:
        return UploadResult(ok=False,
                            message="The .ics file was valid but contained no events.")

    dest = _save_ics(cal_dir, text, filename)
    return UploadResult(ok=True,
                        message=f"Loaded {len(events)} events from {dest.name}.",
                        filename=dest.name)


def upload_url(url: str, cal_dir: Path, data_dir: Path | None = None) -> UploadResult:
    """Fetch a calendar URL and save as .ics.

    If data_dir is provided, also registers the URL for auto-refresh.
    """
    import urllib.request
    import urllib.error

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return UploadResult(ok=False, message="Please enter a full URL starting with http:// or https://")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Presence/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return UploadResult(ok=False, message=f"Could not fetch the URL: {exc.reason}")
    except Exception as exc:
        return UploadResult(ok=False, message=f"Could not fetch the URL: {exc}")

    if not _validate_ics(text):
        return UploadResult(ok=False,
                            message="The URL did not return valid iCal data "
                                    "(no VCALENDAR/VEVENT blocks found).")

    events = parse_ics(text)
    if not events:
        return UploadResult(ok=False,
                            message="The calendar was valid but contained no events.")

    # Detect Canvas feed
    is_canvas = ("/feeds/calendars/" in url
                 or ("X-WR-CALNAME" in text and "canvas" in text.lower()))

    # derive a filename from the URL
    if is_canvas:
        filename = "canvas_feed.ics"
    else:
        slug = re.sub(r"[^a-zA-Z0-9]", "_", url.split("//")[-1])[:40]
        filename = f"{slug}.ics"
    dest = _save_ics(cal_dir, text, filename)

    # register for auto-refresh
    if data_dir is not None:
        from presence.connectors.cal_feeds import add_feed
        label = "Canvas LMS" if is_canvas else ""
        add_feed(data_dir, url, dest.name, label=label)

    return UploadResult(ok=True,
                        message=f"Fetched {len(events)} events and saved as {dest.name}.",
                        filename=dest.name)


def add_manual_deadline(cal_dir: Path, summary: str, due_date: str,
                        due_time: str = "", course: str = "") -> UploadResult:
    """Add a manual deadline as a zero-duration VEVENT to manual_deadlines.ics."""
    cal_dir.mkdir(parents=True, exist_ok=True)
    dest = cal_dir / "manual_deadlines.ics"

    # Parse date/time
    try:
        if due_time:
            dt = datetime.strptime(f"{due_date} {due_time}", "%Y-%m-%d %H:%M")
        else:
            dt = datetime.strptime(due_date, "%Y-%m-%d")
    except ValueError:
        return UploadResult(ok=False, message="Invalid date or time format.")

    label = f"{summary} [{course}]" if course else summary
    dt_str = dt.strftime("%Y%m%dT%H%M%S")
    uid = uuid.uuid4().hex

    vevent = (
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}@presence\r\n"
        f"DTSTART:{dt_str}\r\n"
        f"DTEND:{dt_str}\r\n"
        f"SUMMARY:{label}\r\n"
        "END:VEVENT\r\n"
    )

    if dest.exists():
        # Insert new VEVENT before the closing END:VCALENDAR
        text = dest.read_text(encoding="utf-8")
        text = text.replace("END:VCALENDAR", vevent + "END:VCALENDAR")
        dest.write_text(text, encoding="utf-8")
    else:
        text = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Presence//ManualDeadlines//EN\r\n"
            f"{vevent}"
            "END:VCALENDAR\r\n"
        )
        dest.write_text(text, encoding="utf-8")

    return UploadResult(ok=True, message=f"Deadline added: {label} on {due_date}.",
                        filename="manual_deadlines.ics")


PARSE_SYSTEM_PROMPT = """\
You are a schedule parser. Extract all class/event entries from the text below.
Return ONLY a JSON array of objects with these fields:
- summary (string): class or event name
- date (string): YYYY-MM-DD format
- start_time (string): HH:MM in 24-hour format
- end_time (string): HH:MM in 24-hour format
- location (string): room or building, empty string if unknown
- recurrence (string): "WEEKLY;COUNT=N" if it repeats weekly for N weeks, empty string if one-time

If the timetable shows a recurring weekly schedule without an end date, use COUNT=15 (one semester).
If year is not specified, assume the current academic year.
Return valid JSON only, no markdown fences or extra text."""


def _events_to_ics(events: list[ScheduleEvent]) -> str:
    """Generate a minimal .ics file from parsed events."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Presence//Schedule//EN"]
    for ev in events:
        dt_start = ev.date.replace("-", "") + "T" + ev.start_time.replace(":", "") + "00"
        dt_end = ev.date.replace("-", "") + "T" + ev.end_time.replace(":", "") + "00"
        uid = uuid.uuid4().hex
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}@presence")
        lines.append(f"DTSTART:{dt_start}")
        lines.append(f"DTEND:{dt_end}")
        lines.append(f"SUMMARY:{ev.summary}")
        if ev.location:
            lines.append(f"LOCATION:{ev.location}")
        if ev.recurrence:
            # e.g. "WEEKLY;COUNT=15" → "RRULE:FREQ=WEEKLY;COUNT=15"
            parts = ev.recurrence.split(";")
            freq = parts[0] if parts else "WEEKLY"
            count = parts[1] if len(parts) > 1 else "COUNT=15"
            lines.append(f"RRULE:FREQ={freq};{count}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def upload_pdf(pdf_bytes: bytes, filename: str, cal_dir: Path,
               data_dir: Path) -> UploadResult:
    """Extract text from PDF, call LLM to parse schedule, save as .ics."""
    from presence.app.cvparse import extract_text

    try:
        text = extract_text(pdf_bytes)
    except Exception:
        return UploadResult(ok=False, message="Could not read the PDF. Is it a valid PDF file?")

    if not text.strip():
        return UploadResult(ok=False, message="The PDF appears to be empty or image-only.")

    # Build a provider from app config
    try:
        from presence.core.config import load_app
        from presence.core.secrets import get_secret
        from presence.core.providers import AnthropicProvider, OpenAICompatProvider

        app_cfg = load_app(data_dir)
    except Exception:
        return UploadResult(ok=False,
                            message="Model not configured. Set up a model in Setup first "
                                    "— it's needed to parse PDF timetables.")

    # pick the first available agent's provider
    agent_cfg = next(iter(app_cfg.agents.values()), None)
    if agent_cfg is None:
        return UploadResult(ok=False, message="No agent configured. Set up a model first.")

    provider_cfg = app_cfg.providers.get(agent_cfg.provider)
    if provider_cfg is None:
        return UploadResult(ok=False, message="Provider not found in config.")

    if provider_cfg.kind == "anthropic":
        api_key = get_secret(provider_cfg.api_key_secret or "ANTHROPIC_API_KEY", data_dir)
        if not api_key:
            return UploadResult(ok=False, message="Anthropic API key not set.")
        provider = AnthropicProvider(api_key=api_key)
    else:
        api_key = ""
        if provider_cfg.api_key_secret:
            api_key = get_secret(provider_cfg.api_key_secret, data_dir) or ""
        provider = OpenAICompatProvider(api_key=api_key or "unused",
                                        base_url=provider_cfg.base_url)

    # Call the LLM
    try:
        resp = provider.complete(
            model=agent_cfg.model,
            system=PARSE_SYSTEM_PROMPT,
            transcript=[{"role": "user", "content": text[:8000]}],
            tools=[],
            max_output_tokens=2048,
        )
    except Exception as exc:
        return UploadResult(ok=False, message=f"LLM call failed: {exc}")

    # Parse JSON from response
    raw = resp.text.strip()
    # strip markdown fences if present
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return UploadResult(ok=False,
                            message="The model returned invalid JSON. Try again or use a .ics file.")

    if not isinstance(parsed, list) or not parsed:
        return UploadResult(ok=False,
                            message="The model found no events in the PDF.")

    events: list[ScheduleEvent] = []
    for item in parsed:
        try:
            events.append(ScheduleEvent(**item))
        except Exception:
            continue

    if not events:
        return UploadResult(ok=False,
                            message="Could not parse any events from the model's response.")

    # Generate and save .ics
    ics_text = _events_to_ics(events)
    stem = Path(filename).stem
    dest = _save_ics(cal_dir, ics_text, f"{stem}.ics")
    total = len(parse_ics(ics_text))
    return UploadResult(ok=True,
                        message=f"Parsed {len(events)} events from PDF, "
                                f"saved as {dest.name} ({total} occurrences with recurrence).",
                        filename=dest.name)
