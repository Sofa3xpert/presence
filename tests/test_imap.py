"""Tests for the IMAP email connector."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from presence.connectors.imap import (
    EmailMessage,
    IMAPError,
    _decode_header,
    _snippet,
    _strip_html,
    connect,
    fetch_recent,
    load_email_config,
    save_email_config,
)

# ------------------------------------------------------------------ helpers

class TestDecodeHeader:
    def test_plain(self):
        assert _decode_header("Hello World") == "Hello World"

    def test_none(self):
        assert _decode_header(None) == ""

    def test_empty(self):
        assert _decode_header("") == ""

    def test_encoded(self):
        # RFC 2047 encoded subject
        raw = "=?utf-8?B?0J/RgNC40LLQtdGC?="  # "Привет" in base64
        result = _decode_header(raw)
        assert "Привет" in result


class TestStripHtml:
    def test_basic(self):
        assert _strip_html("<p>Hello</p>") == "Hello"

    def test_br_tags(self):
        assert "Hello\nWorld" in _strip_html("Hello<br>World")

    def test_nested(self):
        result = _strip_html("<div><p>Hi</p><p>there</p></div>")
        assert "Hi" in result
        assert "there" in result


class TestSnippet:
    def test_short(self):
        assert _snippet("short text") == "short text"

    def test_long(self):
        text = "word " * 100
        result = _snippet(text, max_len=50)
        assert len(result) <= 55  # 50 + room for "..."
        assert result.endswith("...")


# ------------------------------------------------------------------ model

class TestEmailMessage:
    def test_str_unread(self):
        msg = EmailMessage(
            subject="Exam reminder",
            sender="prof@uni.edu",
            date=datetime.now(tz=UTC),
            is_unread=True,
        )
        assert "[*]" in str(msg)
        assert "Exam reminder" in str(msg)

    def test_age_hours(self):
        from datetime import timedelta
        old = datetime.now(tz=UTC) - timedelta(hours=3)
        msg = EmailMessage(subject="Old", sender="x", date=old)
        assert 2.9 < msg.age_hours < 3.5


# ------------------------------------------------------------------ connect

class TestConnect:
    @patch("presence.connectors.imap.imaplib.IMAP4_SSL")
    def test_success(self, mock_imap):
        mock_conn = MagicMock()
        mock_imap.return_value = mock_conn
        result = connect("imap.test.com", "user", "pass")
        assert result == mock_conn
        mock_conn.login.assert_called_once_with("user", "pass")

    @patch("presence.connectors.imap.imaplib.IMAP4_SSL")
    def test_connection_error(self, mock_imap):
        mock_imap.side_effect = OSError("refused")
        with pytest.raises(IMAPError, match="Cannot connect"):
            connect("bad.host", "user", "pass")

    @patch("presence.connectors.imap.imaplib.IMAP4_SSL")
    def test_login_error(self, mock_imap):
        import imaplib
        mock_conn = MagicMock()
        mock_conn.login.side_effect = imaplib.IMAP4.error("bad creds")
        mock_imap.return_value = mock_conn
        with pytest.raises(IMAPError, match="Login failed"):
            connect("imap.test.com", "user", "wrong")


# ---------------------------------------------------------------- fetch

class TestFetchRecent:
    def _make_raw_email(self, subject="Test", sender="a@b.com", body="Hello"):
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = sender
        msg["Date"] = "Mon, 22 Sep 2026 10:00:00 +0000"
        return msg.as_bytes()

    @patch("presence.connectors.imap.imaplib.IMAP4_SSL")
    def test_fetch_messages(self, mock_imap_cls):
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"5"])
        conn.search.return_value = ("OK", [b"1 2"])
        raw = self._make_raw_email("Important", "prof@uni.edu", "Read this")
        conn.fetch.return_value = ("OK", [(b"1 (FLAGS (\\Seen) RFC822 {100}", raw)])
        mock_imap_cls.return_value = conn

        results = fetch_recent(conn, folder="INBOX", since_days=1)
        assert len(results) == 2
        assert results[0].subject == "Important"

    @patch("presence.connectors.imap.imaplib.IMAP4_SSL")
    def test_empty_inbox(self, mock_imap_cls):
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"0"])
        conn.search.return_value = ("OK", [b""])
        results = fetch_recent(conn)
        assert results == []


# -------------------------------------------------------- config persistence

class TestConfig:
    def test_save_and_load(self, tmp_path):
        save_email_config(tmp_path, "imap.gmail.com", 993, "me@gmail.com", "INBOX")
        cfg = load_email_config(tmp_path)
        assert cfg is not None
        assert cfg["host"] == "imap.gmail.com"
        assert cfg["user"] == "me@gmail.com"
        assert cfg["port"] == 993

    def test_load_missing(self, tmp_path):
        assert load_email_config(tmp_path) is None
