"""End-to-end through the command line, with a fake source and a fake Telegram."""

import json
import os

import pytest

from presence import cli, cycle
from presence.adapters import telegram
from presence.adapters.telegram import chunks, latest_chat_id
from presence.connectors import Posting

SOURCES = """sources:
  - {id: figma, provider: greenhouse, label: Figma, config: {board: figma}}
  - {id: palantir, provider: lever, label: Palantir, config: {board: palantir}}
"""


def _confirm(data):
    p = data / "profile.yaml"
    p.write_text(p.read_text().replace("confirmed: false", "confirmed: true"))
    (data / "sources.yaml").write_text(SOURCES)


def test_init_writes_neutral_defaults(tmp_path):
    assert cli.main(["init", str(tmp_path)]) == 0
    sources = (tmp_path / "sources.yaml").read_text()
    assert "sources: []" in sources and "figma" not in sources.lower()
    search = (tmp_path / "search.yaml").read_text()
    assert "locations: []" in search and "title_include: []" in search
    assert "London" not in search.split("#")[0]
    for name in cli.EXAMPLES:
        assert "chmod" not in (tmp_path / name).read_text()


def test_init_check_and_refusal_before_confirmation(tmp_path, capsys):
    assert cli.main(["init", str(tmp_path)]) == 0
    if os.name != "nt":
        assert (tmp_path / "secrets.env").stat().st_mode & 0o777 == 0o600
    capsys.readouterr()
    assert cli.main(["check", str(tmp_path)]) == 1  # profile unconfirmed
    out = capsys.readouterr().out
    assert "Confirm your profile first in Setup, step 4" in out
    assert "profile.yaml" not in out and "edit" not in out
    assert cli.main(["cycle", str(tmp_path)]) == 1  # charter rule 2: refuses to run


def test_cycle_fetches_filters_tracks_and_briefs(tmp_path, capsys, monkeypatch):
    cli.main(["init", str(tmp_path)])
    _confirm(tmp_path)
    assert cli.main(["check", str(tmp_path)]) == 0

    def fake_run_sources(sources, search, seen=None, connectors=None):
        assert [s.id for s in sources] == ["figma", "palantir"]
        return ([Posting(company="Figma", title="Software Engineer, AI", location="London",
                         url="https://boards.greenhouse.io/figma/jobs/1", source="greenhouse")],
                {"palantir": "HTTPError: 503"})

    monkeypatch.setattr(cycle, "run_sources", fake_run_sources)
    assert cli.main(["cycle", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 new role" in out and "Figma: Software Engineer, AI" in out
    assert "Sources that failed: palantir" in out and "Nothing was sent on your behalf" in out
    assert (tmp_path / "tracker.db").exists()
    assert json.loads((tmp_path / "seen.json").read_text())  # seen-state persisted
    # second cycle: same posting is seen → no new roles, no duplicate
    cli.main(["cycle", str(tmp_path)])
    assert "No new roles today" in capsys.readouterr().out


def test_cycle_sends_via_telegram_when_paired(tmp_path, monkeypatch):
    cli.main(["init", str(tmp_path)])
    _confirm(tmp_path)
    (tmp_path / "secrets.env").write_text("TELEGRAM_BOT_TOKEN=t0k\nTELEGRAM_CHAT_ID=42\n")
    monkeypatch.setattr(cycle, "run_sources", lambda *a, **k: ([], {}))
    sent = []
    monkeypatch.setattr(telegram, "_call",
                        lambda token, method, **p: sent.append((method, p)) or {})
    assert cli.main(["cycle", str(tmp_path), "--send"]) == 0
    assert sent[0][0] == "sendMessage" and sent[0][1]["chat_id"] == 42
    assert "No new roles today" in sent[0][1]["text"]


def test_telegram_chunking_and_pairing_parse():
    long = "\n".join(f"line {i} " + "x" * 80 for i in range(120))
    parts = chunks(long)
    assert len(parts) > 1 and all(len(p) <= 4000 for p in parts)
    assert "".join(parts).replace("\n", "") == long.replace("\n", "")
    updates = [{"update_id": 1, "message": {"chat": {"id": 7}, "text": "hi"}},
               {"update_id": 2, "edited_message": {"chat": {"id": 9}, "text": "hey"}}]
    assert latest_chat_id(updates) == 9
    assert latest_chat_id([]) is None
    with pytest.raises(telegram.TelegramError, match="pair"):
        telegram.TelegramMessenger("", "")
