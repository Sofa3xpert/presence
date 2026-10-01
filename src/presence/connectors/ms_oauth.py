"""Microsoft OAuth2 for IMAP — device code flow.

The user experience:
1. Click "Connect Outlook" on the student page
2. See a code like "ABC123" and a link to microsoft.com/devicelogin
3. Enter the code, sign in with university account + 2FA
4. Page auto-detects success, email is connected

No Azure app registration needed — uses Thunderbird's public client ID
which Microsoft has pre-approved for IMAP access on all tenants
(including locked-down university ones).
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger("presence.ms_oauth")

# Microsoft identity platform endpoints (works for work/school + personal)
AUTHORITY = "https://login.microsoftonline.com/common"
DEVICE_CODE_URL = f"{AUTHORITY}/oauth2/v2.0/devicecode"
TOKEN_URL = f"{AUTHORITY}/oauth2/v2.0/token"

# Thunderbird's public client ID — pre-authorized by Microsoft for
# IMAP/SMTP/EWS access, works on locked-down university tenants.
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

# Scopes: read-only IMAP access + offline refresh + openid to get user email
SCOPES = "https://outlook.office365.com/IMAP.AccessAsUser.All offline_access openid email"

TOKENS_FILE = "student_email_tokens.json"


# ----------------------------------------------------------- device code flow

def start_device_flow() -> dict:
    """Start the device code flow.

    Returns dict with: user_code, device_code, verification_uri,
    expires_in, interval.
    """
    resp = requests.post(DEVICE_CODE_URL, data={
        "client_id": CLIENT_ID,
        "scope": SCOPES,
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()


def poll_for_token(device_code: str, interval: int = 5, timeout: int = 120) -> dict | None:
    """Poll Microsoft until the user completes login.

    Returns token dict on success, None on timeout/denial.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(interval)
        resp = requests.post(TOKEN_URL, data={
            "client_id": CLIENT_ID,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
        }, timeout=15)
        data = resp.json()
        if "access_token" in data:
            return data
        error = data.get("error", "")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 1
            continue
        # expired_token, authorization_declined, bad_verification_code
        log.warning("Device code flow ended: %s", error)
        return None
    return None


def refresh_access_token(refresh_token: str) -> dict:
    """Use a refresh token to get a new access token."""
    resp = requests.post(TOKEN_URL, data={
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": SCOPES,
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()


def xoauth2_string(user: str, access_token: str) -> str:
    """Build the XOAUTH2 authentication string for IMAP."""
    auth = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return base64.b64encode(auth.encode()).decode()


# --------------------------------------------------------- token persistence

def save_tokens(data: Path, tokens: dict, user: str) -> None:
    """Save OAuth tokens to the data directory."""
    store = {
        "user": user,
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": int(time.time()) + tokens.get("expires_in", 3600),
    }
    (data / TOKENS_FILE).write_text(json.dumps(store, indent=2))


def load_tokens(data: Path) -> dict | None:
    """Load saved tokens. Returns None if not configured."""
    path = data / TOKENS_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def delete_tokens(data: Path) -> None:
    """Remove saved tokens."""
    path = data / TOKENS_FILE
    if path.exists():
        path.unlink()


def get_valid_access_token(data: Path) -> tuple[str, str] | None:
    """Get a valid access token, refreshing if needed.

    Returns (user, access_token) or None if not configured.
    """
    tokens = load_tokens(data)
    if not tokens or not tokens.get("refresh_token"):
        return None

    user = tokens["user"]
    # refresh if expired or about to expire (60s buffer)
    if time.time() >= tokens.get("expires_at", 0) - 60:
        try:
            new = refresh_access_token(tokens["refresh_token"])
            save_tokens(data, new, user)
            return user, new["access_token"]
        except Exception:
            log.warning("OAuth token refresh failed", exc_info=True)
            return None

    return user, tokens["access_token"]


def extract_email_from_jwt(id_token: str) -> str | None:
    """Decode JWT payload (without verification) to get the email."""
    if not id_token:
        return None
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        return decoded.get("preferred_username") or decoded.get("email") or decoded.get("upn")
    except Exception:
        return None


