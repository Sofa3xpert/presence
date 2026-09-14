"""The `presence` command — developer preview.

    presence init <data_dir>          scaffold a data folder with example config
    presence check <data_dir>         validate config, sources and delivery
    presence cycle <data_dir> [--send]  fetch → filter → tracker → brief (Telegram with --send)
    presence telegram pair <data_dir> capture your chat id after you message your bot
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from presence import __version__
from presence.adapters import ConsoleMessenger, TelegramError, TelegramMessenger, pair
from presence.agents.brief import compose_brief
from presence.connectors import SeenPostings, run_sources
from presence.core.config import ConfigError, load_app, load_profile, load_search, load_sources
from presence.core.secrets import get_secret
from presence.tracker import Tracker
from presence.tracker.conventions import link_key

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
        load_profile(data)  # refuses an unconfirmed profile — charter rule 2
        search, sources = load_search(data), load_sources(data)
    except ConfigError as exc:
        print(exc)
        return 1
    seen = SeenPostings(data / "seen.json")
    tracker = Tracker(data / "tracker.db")
    postings, errors = run_sources(sources, search, seen=seen.keys())
    new_jobs = []
    for p in postings:
        job, created = tracker.ingest(p.candidate)
        if created:
            new_jobs.append(job)
    seen.mark([link_key(p.url) for p in postings if p.url])
    brief = compose_brief(tracker, new_jobs, errors, today=date.today())
    messenger = (_telegram(data) if send else None) or ConsoleMessenger()
    try:
        messenger.send(brief)
    except TelegramError as exc:
        print(f"telegram delivery failed: {exc}\n\n{brief}")
        return 1
    tracker.close()
    if send and messenger.name == "console":
        print("(not paired with Telegram — printed instead)")
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="presence", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"presence {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "check", "cycle"):
        sp = sub.add_parser(name)
        sp.add_argument("data", type=Path)
        if name == "cycle":
            sp.add_argument("--send", action="store_true", help="deliver via Telegram")
    tg = sub.add_parser("telegram").add_subparsers(dest="tg", required=True)
    tg.add_parser("pair").add_argument("data", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args.data)
    if args.cmd == "check":
        return cmd_check(args.data)
    if args.cmd == "cycle":
        return cmd_cycle(args.data, args.send)
    return cmd_pair(args.data)


if __name__ == "__main__":
    sys.exit(main())
