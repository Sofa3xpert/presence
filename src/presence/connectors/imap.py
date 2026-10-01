"""IMAP email connector — read-only access to a university inbox.

Fetches recent emails and returns structured summaries for the student
daily brief.  Uses only Python's built-in imaplib + email modules.
"""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

log = logging.getLogger("presence.imap")

# IMAP presets for common university email providers
PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {"host": "imap.gmail.com", "port": 993},
    "outlook": {"host": "outlook.office365.com", "port": 993},
    "yahoo": {"host": "imap.mail.yahoo.com", "port": 993},
}


class EmailMessage(BaseModel):
    """One email — enough context for the daily brief."""

    subject: str
    sender: str  # "Name <addr>" or just addr
    date: datetime
    snippet: str = ""  # first ~200 chars of body text
    is_unread: bool = False

    @property
    def age_hours(self) -> float:
        now = datetime.now(tz=timezone.utc)
        dt = self.date if self.date.tzinfo else self.date.replace(tzinfo=timezone.utc)
        return max(0, (now - dt).total_seconds() / 3600)

    def __str__(self) -> str:
        flag = "*" if self.is_unread else " "
        return f"[{flag}] {self.sender}: {self.subject}"


class IMAPError(Exception):
    """Raised when IMAP operations fail."""


# ------------------------------------------------------------------ helpers

def _decode_header(raw: str | None) -> str:
    """Decode an RFC 2047 encoded header value."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded: list[str] = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded).strip()


def _extract_text(msg: email.message.Message) -> str:
    """Extract plain-text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and part.get("Content-Disposition") != "attachment":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # fallback: try text/html and strip tags
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html" and part.get("Content-Disposition") != "attachment":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    return _strip_html(html)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                return _strip_html(text)
            return text
    return ""


def _strip_html(html: str) -> str:
    """Rough HTML→text: strip tags, collapse whitespace."""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_date(msg: email.message.Message) -> datetime:
    """Parse the Date header into a datetime."""
    raw = msg.get("Date", "")
    parsed = email.utils.parsedate_to_datetime(raw) if raw else None
    return parsed or datetime.now(tz=timezone.utc)


def _snippet(text: str, max_len: int = 200) -> str:
    """First max_len chars of text, cleaned up."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


# ----------------------------------------------------------- main functions

def connect(
    host: str,
    user: str,
    password: str,
    port: int = 993,
    timeout: int = 15,
) -> imaplib.IMAP4_SSL:
    """Open an authenticated IMAP connection (basic auth)."""
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    except Exception as exc:
        raise IMAPError(f"Cannot connect to {host}:{port} — {exc}") from exc
    try:
        conn.login(user, password)
    except imaplib.IMAP4.error as exc:
        raise IMAPError(f"Login failed for {user} — {exc}") from exc
    return conn


def connect_oauth(
    user: str,
    access_token: str,
    host: str = "outlook.office365.com",
    port: int = 993,
    timeout: int = 15,
) -> imaplib.IMAP4_SSL:
    """Open an IMAP connection using OAuth2 (XOAUTH2)."""
    from presence.connectors.ms_oauth import xoauth2_string

    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
    except Exception as exc:
        raise IMAPError(f"Cannot connect to {host}:{port} — {exc}") from exc
    try:
        auth_string = xoauth2_string(user, access_token)
        conn.authenticate("XOAUTH2", lambda _: auth_string.encode())
    except imaplib.IMAP4.error as exc:
        raise IMAPError(f"OAuth login failed for {user} — {exc}") from exc
    return conn


def fetch_recent(
    conn: imaplib.IMAP4_SSL,
    folder: str = "INBOX",
    since_days: int = 1,
    limit: int = 20,
) -> list[EmailMessage]:
    """Fetch recent emails from a folder.

    Returns up to `limit` messages from the last `since_days` days,
    newest first.
    """
    try:
        status, _ = conn.select(folder, readonly=True)
        if status != "OK":
            raise IMAPError(f"Cannot open folder '{folder}'")
    except imaplib.IMAP4.error as exc:
        raise IMAPError(f"Cannot open folder '{folder}' — {exc}") from exc

    since = (date.today() - timedelta(days=since_days)).strftime("%d-%b-%Y")
    status, data = conn.search(None, f'(SINCE "{since}")')
    if status != "OK" or not data[0]:
        return []

    msg_ids = data[0].split()
    # newest first, cap at limit
    msg_ids = list(reversed(msg_ids[-limit:]))

    messages: list[EmailMessage] = []
    for mid in msg_ids:
        try:
            status, msg_data = conn.fetch(mid, "(FLAGS RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            # msg_data may have different shapes; find the tuple with RFC822
            raw_email = None
            flags_raw = b""
            for part in msg_data:
                if isinstance(part, tuple):
                    raw_email = part[1]
                    flags_raw = part[0]
            if raw_email is None:
                continue

            msg = email.message_from_bytes(raw_email)
            is_unread = b"\\Seen" not in flags_raw
            body = _extract_text(msg)

            messages.append(EmailMessage(
                subject=_decode_header(msg.get("Subject")),
                sender=_decode_header(msg.get("From")),
                date=_parse_date(msg),
                snippet=_snippet(body),
                is_unread=is_unread,
            ))
        except Exception:
            log.debug("Failed to parse message %s", mid, exc_info=True)
            continue

    return messages


def fetch_emails(
    host: str,
    user: str,
    password: str,
    port: int = 993,
    folder: str = "INBOX",
    since_days: int = 1,
    limit: int = 20,
) -> list[EmailMessage]:
    """All-in-one: connect (basic auth), fetch, disconnect."""
    conn = connect(host, user, password, port)
    try:
        return fetch_recent(conn, folder=folder, since_days=since_days, limit=limit)
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def fetch_emails_oauth(
    data_dir: Path,
    folder: str = "INBOX",
    since_days: int = 1,
    limit: int = 20,
) -> list[EmailMessage]:
    """All-in-one: get OAuth token, connect, fetch, disconnect."""
    from presence.connectors.ms_oauth import get_valid_access_token

    result = get_valid_access_token(data_dir)
    if result is None:
        raise IMAPError("OAuth tokens expired or not configured — reconnect email")
    user, access_token = result
    conn = connect_oauth(user, access_token)
    try:
        return fetch_recent(conn, folder=folder, since_days=since_days, limit=limit)
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ----------------------------------------- config helpers for student setup

def load_email_config(data: Path) -> dict[str, str] | None:
    """Load email connector config.

    Checks OAuth tokens first (student_email_tokens.json), then falls back
    to basic auth config (student_email.json).
    Returns dict with at least 'user' and 'auth' ('oauth' or 'basic'), or None.
    """
    import json

    from presence.connectors.ms_oauth import load_tokens

    # OAuth takes priority
    tokens = load_tokens(data)
    if tokens and tokens.get("user"):
        return {
            "auth": "oauth",
            "user": tokens["user"],
            "host": "outlook.office365.com",
            "port": 993,
            "folder": "INBOX",
        }

    # Basic auth fallback
    path = data / "student_email.json"
    if not path.exists():
        return None
    try:
        cfg = json.loads(path.read_text())
        if cfg.get("host") and cfg.get("user"):
            cfg["auth"] = "basic"
            return cfg
    except Exception:
        pass
    return None


def save_email_config(data: Path, host: str, port: int, user: str, folder: str = "INBOX") -> None:
    """Save email connector settings (no password — that goes to secrets)."""
    import json

    path = data / "student_email.json"
    path.write_text(json.dumps({
        "host": host,
        "port": port,
        "user": user,
        "folder": folder,
    }, indent=2))
