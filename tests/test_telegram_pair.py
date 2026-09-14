"""Telegram pairing by deep link: identity, code matching, QR, and the app's poll."""

import json

from presence.adapters import telegram
from presence.app import qr, server
from presence.app.config_io import read_secrets


def _update(uid, text, chat_id=42, name="Ada"):
    return {
        "update_id": uid,
        "message": {
            "text": text,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"first_name": name},
        },
    }


def test_find_start_matches_only_our_code():
    ups = [
        _update(1, "hello", chat_id=7, name="Stranger"),
        _update(2, "/start wrong", chat_id=8),
        _update(3, "/start abc123", chat_id=42),
    ]
    hit = telegram.find_start(ups, "abc123")
    assert hit == {"chat_id": 42, "name": "Ada", "update_id": 3}
    assert telegram.find_start(ups, "zzz") is None
    assert (
        telegram.pairing_link("ada_presence_bot", "abc123")
        == "https://t.me/ada_presence_bot?start=abc123"
    )


def test_pair_once_acks_the_update(monkeypatch):
    calls = []

    def fake_call(token, method, **payload):
        calls.append((method, payload))
        return (
            [_update(9, "/start c0de")]
            if method == "getUpdates" and "offset" not in payload
            else []
        )

    monkeypatch.setattr(telegram, "_call", fake_call)
    assert telegram.pair_once("t", "c0de")["chat_id"] == 42
    assert ("getUpdates", {"offset": 10, "timeout": 0}) in calls
    assert telegram.pair("t", wait_seconds=1, poll=0.01, code="c0de") == 42


def test_qr_is_inline_svg():
    assert qr.data_uri("https://t.me/BotFather").startswith("data:image/svg+xml")


def test_app_save_then_poll_pairs_only_with_code(tmp_path, monkeypatch):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    c = app.test_client()
    monkeypatch.setattr(
        server, "get_me", lambda token: {"username": "ada_presence_bot", "first_name": "Presence"}
    )
    r = c.post(
        "/setup/telegram", data={"action": "save", "token": "123:abc"}, follow_redirects=True
    )
    assert b"bot @ada_presence_bot saved" in r.data
    bot = json.loads((tmp_path / "telegram_bot.json").read_text())
    assert bot["username"] == "ada_presence_bot" and len(bot["pair_code"]) >= 12
    page = c.get("/").data.decode()
    assert f"https://t.me/ada_presence_bot?start={bot['pair_code']}" in page
    assert 'src="data:image/svg+xml' in page and "waiting for Start" in page
    # a stranger's message does not pair; the coded Start does
    monkeypatch.setattr(server, "pair_once", lambda token, code: None)
    assert c.get("/telegram/pair/status").get_json() == {"paired": False}
    monkeypatch.setattr(
        server,
        "pair_once",
        lambda token, code: (
            {"chat_id": 42, "name": "Ada", "update_id": 3} if code == bot["pair_code"] else None
        ),
    )
    j = c.get("/telegram/pair/status").get_json()
    assert j["paired"] is True and j["name"] == "Ada"
    assert read_secrets(tmp_path)["TELEGRAM_CHAT_ID"] == "42"
    assert b"paired \xc2\xb7 Ada" in c.get("/").data
    # once paired the poll answers from the secrets, no Telegram call
    monkeypatch.setattr(server, "pair_once", lambda token, code: 1 / 0)
    assert c.get("/telegram/pair/status").get_json()["paired"] is True


def test_bad_token_is_not_stored(tmp_path, monkeypatch):
    app = server.create_app(tmp_path)
    app.config["TESTING"] = True
    c = app.test_client()

    def boom(token):
        raise telegram.TelegramError("getMe: Unauthorized")

    monkeypatch.setattr(server, "get_me", boom)
    r = c.post("/setup/telegram", data={"action": "save", "token": "bad"}, follow_redirects=True)
    assert b"Unauthorized" in r.data and "TELEGRAM_BOT_TOKEN" not in read_secrets(tmp_path)


def test_network_errors_never_show_the_token(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("Max retries exceeded with url: /bot123:SECRET/getMe")
    monkeypatch.setattr(telegram.requests, "post", boom)
    try:
        telegram._call("123:SECRET", "getMe")
    except telegram.TelegramError as exc:
        assert "SECRET" not in str(exc) and "<token>" in str(exc)
    else:
        raise AssertionError("expected a TelegramError")
