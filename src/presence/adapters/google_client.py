"""The Google OAuth client Presence ships with.

A release build carries its own Desktop-app OAuth client so that "Connect
Google" is one click for the person. The two values below are placeholders in
the source tree; the release workflow swaps them in with
``scripts/inject_google_client.py`` from repository secrets. They are never
committed (``tests/test_google_client.py`` greps the tree for that).

For a local try-out set ``PRESENCE_GOOGLE_CLIENT_ID`` and
``PRESENCE_GOOGLE_CLIENT_SECRET`` in the environment instead.

Google's own guidance: for installed apps the client secret "is obviously not
treated as a secret" (it lives in the app), but it must never sit in a public
repository — hence placeholders here and injection at build time."""

from __future__ import annotations

import os
from typing import Any

CLIENT_ID = "__PRESENCE_GOOGLE_CLIENT_ID__"
CLIENT_SECRET = "__PRESENCE_GOOGLE_CLIENT_SECRET__"

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _placeholder(value: str) -> bool:
    return not value or value.startswith("__")


def client_id() -> str:
    return os.environ.get("PRESENCE_GOOGLE_CLIENT_ID") or CLIENT_ID


def client_secret() -> str:
    return os.environ.get("PRESENCE_GOOGLE_CLIENT_SECRET") or CLIENT_SECRET


def has_builtin_client() -> bool:
    return not _placeholder(client_id())


def builtin_client_config() -> dict[str, Any] | None:
    """The 'installed' client config google-auth-oauthlib expects, or None
    when this build ships no client (source checkout without injection)."""
    cid, secret = client_id(), client_secret()
    if _placeholder(cid):
        return None
    installed: dict[str, Any] = {
        "client_id": cid,
        "auth_uri": AUTH_URI,
        "token_uri": TOKEN_URI,
        "redirect_uris": ["http://localhost"],
    }
    if not _placeholder(secret):
        installed["client_secret"] = secret
    return {"installed": installed}
