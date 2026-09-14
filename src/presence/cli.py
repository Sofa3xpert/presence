"""The `presence` command — developer preview.

    presence init <data_dir>          scaffold a data folder with example config
    presence check <data_dir>         validate config, sources and delivery
    presence cycle <data_dir> [--send]  fetch → filter → tracker → brief (Telegram with --send)
    presence telegram pair <data_dir> capture your chat id after you message your bot
    presence serve <data_dir> [--port] the local app: guided setup, tracker, run
    presence sheet sync <data_dir>    mirror the tracker to the connected Google Sheet
    presence import <data_dir> <csv>  bring an existing tracker in from a CSV export
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from presence import __version__
from presence.adapters import TelegramError, TelegramMessenger, pair
from presence.core.config import ConfigError, load_app, load_profile, load_search, load_sources
from presence.core.secrets import get_secret
from presence.cycle import run_cycle

EXAMPLES = {
    "profile.yaml": """# Facts Presence may use. Nothing runs until you set confirmed: true.
identity:
  name: Your Name
  email: you@example.org
work_authorization:
  summary: e.g. full right to work in the UK, no sponsorship needed
  needs_sponsorship: false
skills: [python]
confirmed: false
""",
    "sources.yaml": """# Boards to read — published APIs only. One company per source.
# Find the board token in the company's careers URL (e.g. boards.greenhouse.io/<token>).
sources:
  - {id: figma, provider: greenhouse, label: Figma, config: {board: figma}}
  - {id: palantir, provider: lever, label: Palantir, config: {board: palantir}}
""",
    "search.yaml": """# Your filters, applied to every source.
locations: [London, United Kingdom, Remote]
remote_ok: true
title_include: [engineer, scientist, developer, analyst, graduate]
# title_exclude defaults to senior/staff/principal/lead/manager/director titles
blocklist: []
freshness_hours: 336
""",
    "presence.yaml": """# Runtime: model provider(s) and agents. Local model by default.
providers:
  local: {kind: openai-compatible, base_url: "http://localhost:11434/v1"}
agents:
  scout: {provider: local, model: "qwen3.5:9b", at: "08:00"}
  brief: {provider: local, model: "qwen3.5:9b", at: "09:00"}
budget_tokens_per_day: 200000
""",
    "secrets.env": """# Local secrets — never leaves this machine. chmod 600.
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
""",
}


def cmd_init(data: Path) -> int:
    data.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in EXAMPLES.items():
        path = data / name
        if not path.exists():
            path.write_text(body)
            if name == "secrets.env":
                os.chmod(path, 0o600)
            written.append(name)
    print(f"initialised {data} — wrote {', '.join(written) or 'nothing (all present)'}")
    print("next: edit profile.yaml (set confirmed: true), sources.yaml, search.yaml; "
          "then `presence check` and `presence cycle`")
    return 0


def _telegram(data: Path) -> TelegramMessenger | None:
    token = get_secret("TELEGRAM_BOT_TOKEN", data)
    chat = get_secret("TELEGRAM_CHAT_ID", data)
    if token and chat:
        return TelegramMessenger(token, chat)
    return None


def cmd_check(data: Path) -> int:
    problems = 0
    try:
        profile = load_profile(data)
        print(f"profile: {profile.identity.name} — confirmed")
    except ConfigError as exc:
        problems += 1
        print(f"profile: {exc}")
    for label, loader in (("search", load_search), ("app", load_app)):
        try:
            loader(data)
            print(f"{label}: ok")
        except ConfigError as exc:
            problems += 1
            print(f"{label}: {exc}")
    try:
        sources = load_sources(data)
        enabled = [s for s in sources if s.enabled]
        print(f"sources: {len(enabled)} enabled of {len(sources)} "
              f"({', '.join(f'{s.provider}:{s.id}' for s in enabled)})")
    except ConfigError as exc:
        problems += 1
        print(f"sources: {exc}")
    print("telegram: paired" if _telegram(data) else
          "telegram: not paired (brief prints to the console; run `presence telegram pair`)")
    print("check:", "ready" if problems == 0 else f"{problems} problem(s) to fix")
    return 0 if problems == 0 else 1


def cmd_cycle(data: Path, send: bool) -> int:
    try:
        brief, errors, delivered = run_cycle(data, send=send)
    except ConfigError as exc:
        print(exc)
        return 1
    except TelegramError as exc:
        print(f"telegram delivery failed: {exc}")
        return 1
    print(brief)
    if send and not delivered:
        print("(not paired with Telegram — printed instead)")
    return 0


def cmd_serve(data: Path, port: int) -> int:
    from presence.app import create_app  # lazy: Flask only when serving

    if not any((data / n).exists() for n in EXAMPLES):
        cmd_init(data)
    print(f"Presence app: http://127.0.0.1:{port}  (data: {data}) — Ctrl-C to stop")
    create_app(data).run(host="127.0.0.1", port=port, debug=False)
    return 0


def cmd_pair(data: Path) -> int:
    token = get_secret("TELEGRAM_BOT_TOKEN", data)
    if not token:
        print("add TELEGRAM_BOT_TOKEN to secrets.env first (create a bot with @BotFather)")
        return 1
    print("open your bot in Telegram and send it any message — waiting up to 90 s…")
    try:
        chat_id = pair(token)
    except TelegramError as exc:
        print(exc)
        return 1
    if not chat_id:
        print("no message seen — send one to the bot and run pair again")
        return 1
    path = data / "secrets.env"
    lines = [ln for ln in path.read_text().splitlines() if not ln.startswith("TELEGRAM_CHAT_ID=")]
    path.write_text("\n".join(lines + [f"TELEGRAM_CHAT_ID={chat_id}"]) + "\n")
    print(f"paired: chat id saved to {path.name}")
    return 0


def cmd_sheet_sync(data: Path) -> int:
    from presence.adapters import gsheet
    from presence.tracker import Tracker

    tr = load_app(data).tracker
    if tr.backend != "sheet" or not tr.sheet_id:
        print("no Google Sheet connected — choose it in the app (presence serve)")
        return 1
    creds = gsheet.credentials(data)
    if creds is None:
        print("Google is not connected — sign in or add a service account in the app")
        return 1
    tracker = Tracker(data / "tracker.db")
    try:
        res = gsheet.sync(tracker, gsheet.SheetClient(creds), tr.sheet_id, tr.tab,
                          data / gsheet.STATE_FILE)
    except gsheet.SheetError as exc:
        print(exc)
        return 1
    finally:
        tracker.close()
    print("synced:", res.summary())
    for line in res.pulled + res.issues:
        print(" ", line)
    return 0 if not res.issues else 2


def cmd_import(data: Path, csv_path: Path) -> int:
    import csv
    from datetime import date

    from presence.tracker import Tracker
    from presence.tracker.importer import import_rows

    rows = list(csv.reader(csv_path.open(newline="", encoding="utf-8-sig")))
    if len(rows) < 2:
        print("that CSV has no rows under its header")
        return 1
    tracker = Tracker(data / "tracker.db")
    try:
        rep = import_rows(tracker, rows[0], rows[1:], year=date.today().year,
                          source_default="csv-import")
    finally:
        tracker.close()
    print("imported:", rep.summary())
    for line in rep.issues:
        print(" ", line)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="presence", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"presence {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "check", "cycle", "serve"):
        sp = sub.add_parser(name)
        sp.add_argument("data", type=Path)
        if name == "cycle":
            sp.add_argument("--send", action="store_true", help="deliver via Telegram")
        if name == "serve":
            sp.add_argument("--port", type=int, default=8790)
    tg = sub.add_parser("telegram").add_subparsers(dest="tg", required=True)
    tg.add_parser("pair").add_argument("data", type=Path)
    sh = sub.add_parser("sheet").add_subparsers(dest="sheet", required=True)
    sh.add_parser("sync").add_argument("data", type=Path)
    imp = sub.add_parser("import")
    imp.add_argument("data", type=Path)
    imp.add_argument("csv", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args.data)
    if args.cmd == "check":
        return cmd_check(args.data)
    if args.cmd == "cycle":
        return cmd_cycle(args.data, args.send)
    if args.cmd == "serve":
        return cmd_serve(args.data, args.port)
    if args.cmd == "sheet":
        return cmd_sheet_sync(args.data)
    if args.cmd == "import":
        return cmd_import(args.data, args.csv)
    return cmd_pair(args.data)


if __name__ == "__main__":
    sys.exit(main())
