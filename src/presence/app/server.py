"""The local app — setup, tracker, run. Binds to 127.0.0.1 only: your data
never leaves this machine, and neither does this page."""

from __future__ import annotations

import csv
import io
import json
import secrets as pysecrets
from datetime import date
from pathlib import Path
from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from presence.adapters import (
    TelegramError,
    TelegramMessenger,
    get_me,
    gsheet,
    pair,
    pair_once,
    pairing_link,
)
from presence.app import ollama, qr
from presence.app.boards_ui import detect
from presence.app.config_io import read_secrets, read_yaml, write_secret, write_yaml
from presence.app.cvparse import extract_text, guess_fields
from presence.connectors import CATALOG, available
from presence.core.config import ConfigError
from presence.cycle import run_cycle
from presence.tracker import STATUSES, Tracker
from presence.tracker.conventions import ConventionError
from presence.tracker.importer import import_rows

DEFAULT_MODELS = {
    "local": "qwen3.5:9b",
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
}


def _csv(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def sheet_client_for(data: Path) -> Any:
    creds = gsheet.credentials(data)
    return gsheet.SheetClient(creds) if creds is not None else None


def _bot(data: Path) -> dict[str, Any]:
    f = data / "telegram_bot.json"
    return json.loads(f.read_text()) if f.exists() else {}


def _state(data: Path) -> dict[str, Any]:
    app_cfg, profile, search = (
        read_yaml(data / "presence.yaml"),
        read_yaml(data / "profile.yaml"),
        read_yaml(data / "search.yaml"),
    )
    sources = read_yaml(data / "sources.yaml").get("sources", [])
    sec = read_secrets(data)
    bot = _bot(data)
    pair_link = (pairing_link(bot["username"], bot["pair_code"])
                 if bot.get("username") and bot.get("pair_code") else "")
    draft = read_yaml(data / "cv_draft.json") if (data / "cv_draft.json").exists() else {}
    providers = app_cfg.get("providers", {})
    scout = (app_cfg.get("agents") or {}).get("scout", {})
    pname = scout.get("provider", "local")
    pcfg = providers.get(pname, {})
    kind = (
        "local"
        if pcfg.get("kind", "openai-compatible") == "openai-compatible"
        and "localhost" in str(pcfg.get("base_url", "localhost"))
        else pname
    )
    identity = profile.get("identity") or {}
    fields = draft.get("fields") or {}
    return {
        "model": {
            "kind": kind,
            "model": scout.get("model", ""),
            "base_url": pcfg.get("base_url", "http://localhost:11434/v1"),
            "has_key": bool(sec.get("ANTHROPIC_API_KEY") or sec.get("OPENAI_API_KEY")),
        },
        "model_done": bool(scout.get("model")),
        "telegram_token": bool(sec.get("TELEGRAM_BOT_TOKEN")),
        "telegram_paired": bool(sec.get("TELEGRAM_BOT_TOKEN") and sec.get("TELEGRAM_CHAT_ID")),
        "bot": bot,
        "pair_link": pair_link,
        "pair_qr": qr.data_uri(pair_link) if pair_link else "",
        "botfather_qr": qr.data_uri("https://t.me/BotFather"),
        "chat_id": sec.get("TELEGRAM_CHAT_ID", ""),
        "tracker": {"backend": "sqlite", "sheet_id": "", "sheet_url": "", "tab": "Tracker",
                    "share_with": "", **(app_cfg.get("tracker") or {})},
        "google": gsheet.connection(data),
        "oauth_running": gsheet.oauth_status(data)["running"],
        "sheet_last": (json.loads((data / gsheet.STATE_FILE).read_text()).get("last", {})
                       if (data / gsheet.STATE_FILE).exists() else {}),
        "profile": {
            "name": identity.get("name") or fields.get("name", ""),
            "email": identity.get("email") or fields.get("email", ""),
            "phone": (profile.get("contact") or {}).get("phone") or fields.get("phone", ""),
            "work_auth": (profile.get("work_authorization") or {}).get("summary", ""),
            "skills": ", ".join(profile.get("skills") or fields.get("skills") or []),
            "links": ", ".join(profile.get("links") or fields.get("links") or []),
        },
        "profile_confirmed": bool(profile.get("confirmed")),
        "cv_chars": draft.get("text_chars", 0),
        "sources": sources,
        "filters": {
            "locations": ", ".join(search.get("locations") or []),
            "title_include": ", ".join(search.get("title_include") or []),
            "title_exclude": ", ".join(search.get("title_exclude") or []),
            "freshness_days": int(search.get("freshness_hours", 336)) // 24,
        },
    }


def create_app(data: Path) -> Flask:
    data.mkdir(parents=True, exist_ok=True)
    app = Flask(__name__)
    app.secret_key = pysecrets.token_hex(16)

    @app.context_processor
    def _ctx() -> dict[str, Any]:
        return {"data_dir": str(data)}

    @app.get("/")
    def setup():
        return render_template(
            "setup.html",
            page="setup",
            state=_state(data),
            catalog=available(),
            catalog_hold=[c for c in CATALOG if c["status"] == "on_hold"],
        )

    @app.post("/setup/model")
    def setup_model():
        kind = request.form.get("kind", "local")
        model = request.form.get("model", "").strip() or DEFAULT_MODELS[kind]
        cfg = read_yaml(data / "presence.yaml")
        if kind == "local":
            cfg["providers"] = {
                "local": {
                    "kind": "openai-compatible",
                    "base_url": request.form.get("base_url", "").strip()
                    or "http://localhost:11434/v1",
                }
            }
            pname = "local"
        else:
            secret = "ANTHROPIC_API_KEY" if kind == "anthropic" else "OPENAI_API_KEY"
            key = request.form.get("api_key", "").strip()
            if key:
                write_secret(data, secret, key)
            elif not read_secrets(data).get(secret):
                flash("an API key is needed for that provider", "error")
                return redirect(url_for("setup"))
            cfg["providers"] = {
                kind: {
                    "kind": "anthropic" if kind == "anthropic" else "openai-compatible",
                    "api_key_secret": secret,
                }
            }
            pname = kind
        cfg["agents"] = {
            "scout": {"provider": pname, "model": model, "at": "08:00"},
            "brief": {"provider": pname, "model": model, "at": "09:00"},
        }
        cfg.setdefault("budget_tokens_per_day", 200_000)
        write_yaml(data / "presence.yaml", cfg)
        flash(f"model saved: {kind} · {model}")
        return redirect(url_for("setup"))

    @app.get("/ollama/status")
    def ollama_status():
        return jsonify(ollama.status())

    @app.post("/ollama/start")
    def ollama_start():
        flash(ollama.start())
        return redirect(url_for("setup"))

    @app.post("/ollama/pull")
    def ollama_pull():
        model = request.form.get("model", "").strip() or ollama.status()["recommended"]
        return jsonify(ollama.start_pull(model))

    @app.get("/ollama/pull/status")
    def ollama_pull_status():
        return jsonify(ollama.pull_status(request.args.get("model", "")))

    @app.post("/ollama/check")
    def ollama_check():
        model = request.form.get("model", "").strip()
        ok, detail = ollama.readiness(model)
        if ok:
            cfg = read_yaml(data / "presence.yaml")
            cfg["providers"] = {"local": {"kind": "openai-compatible",
                                          "base_url": f"{ollama.DEFAULT_URL}/v1"}}
            cfg["agents"] = {"scout": {"provider": "local", "model": model, "at": "08:00"},
                             "brief": {"provider": "local", "model": model, "at": "09:00"}}
            cfg.setdefault("budget_tokens_per_day", 200_000)
            write_yaml(data / "presence.yaml", cfg)
            flash(f"{model} is ready — {detail}; saved as your model")
        else:
            flash(f"{model}: {detail}", "error")
        return redirect(url_for("setup"))

    @app.post("/setup/telegram")
    def setup_telegram():
        action = request.form.get("action", "save")
        token = request.form.get("token", "").strip() or read_secrets(data).get(
            "TELEGRAM_BOT_TOKEN", ""
        )
        if not token:
            flash("paste your bot token first", "error")
            return redirect(url_for("setup"))
        try:
            if action == "save":
                me = get_me(token)  # the token is proven before anything is stored
                write_secret(data, "TELEGRAM_BOT_TOKEN", token)
                bot = {"username": me["username"], "first_name": me["first_name"],
                       "pair_code": pysecrets.token_urlsafe(9)}
                (data / "telegram_bot.json").write_text(json.dumps(bot))
                flash(f"bot @{me['username']} saved — now pair it from your phone")
            elif action == "pair":
                write_secret(data, "TELEGRAM_BOT_TOKEN", token)
                chat_id = pair(token, wait_seconds=20)
                if not chat_id:
                    flash("no message seen yet — send your bot a message, then click Pair", "error")
                else:
                    write_secret(data, "TELEGRAM_CHAT_ID", str(chat_id))
                    flash(f"paired with chat {chat_id}")
            elif action == "test":
                chat = read_secrets(data).get("TELEGRAM_CHAT_ID", "")
                TelegramMessenger(token, chat).send(
                    "Presence is paired with this chat. Nothing is ever sent on your behalf."
                )
                flash("test message sent")
        except TelegramError as exc:
            flash(str(exc), "error")
        return redirect(url_for("setup"))

    @app.get("/telegram/pair/status")
    def telegram_pair_status():
        sec = read_secrets(data)
        bot = _bot(data)
        if sec.get("TELEGRAM_CHAT_ID"):
            return jsonify({"paired": True, "chat_id": sec["TELEGRAM_CHAT_ID"],
                            "name": bot.get("paired_name", "")})
        token = sec.get("TELEGRAM_BOT_TOKEN", "")
        if not (token and bot.get("pair_code")):
            return jsonify({"paired": False, "reason": "save the token first"})
        try:
            hit = pair_once(token, bot["pair_code"])
        except TelegramError as exc:
            return jsonify({"paired": False, "reason": str(exc)})
        if not hit:
            return jsonify({"paired": False})
        write_secret(data, "TELEGRAM_CHAT_ID", str(hit["chat_id"]))
        bot["paired_name"] = hit["name"]
        (data / "telegram_bot.json").write_text(json.dumps(bot))
        return jsonify({"paired": True, "chat_id": hit["chat_id"], "name": hit["name"]})

    def _save_tracker(**fields: Any) -> dict[str, Any]:
        cfg = read_yaml(data / "presence.yaml")
        tr = cfg.get("tracker") or {}
        tr.update(fields)
        cfg["tracker"] = tr
        write_yaml(data / "presence.yaml", cfg)
        return tr

    def _client_or_flash() -> Any:
        client = sheet_client_for(data)
        if client is None:
            flash("connect Google first: sign in, or add a service account", "error")
        return client

    @app.post("/setup/tracker")
    def setup_tracker():
        tr = _save_tracker(backend=request.form.get("backend", "sqlite"))
        flash("tracker: " + ("Google Sheet mirror" if tr["backend"] == "sheet"
                             else "SQLite on this machine"))
        return redirect(url_for("setup"))

    @app.post("/google/service-account")
    def google_service_account():
        f = request.files.get("key")
        if f is None or not f.filename:
            flash("choose the service-account key file", "error")
            return redirect(url_for("setup"))
        try:
            email = gsheet.save_service_account(data, f.read())
        except gsheet.SheetError as exc:
            flash(str(exc), "error")
            return redirect(url_for("setup"))
        flash(f"service account saved: {email}")
        return redirect(url_for("setup"))

    @app.post("/google/oauth/client")
    def google_oauth_client():
        f = request.files.get("client")
        if f is None or not f.filename:
            flash("choose the OAuth client file", "error")
            return redirect(url_for("setup"))
        try:
            gsheet.save_oauth_client(data, f.read())
        except gsheet.SheetError as exc:
            flash(str(exc), "error")
            return redirect(url_for("setup"))
        flash("OAuth client saved — now press Connect Google")
        return redirect(url_for("setup"))

    @app.post("/google/oauth/start")
    def google_oauth_start():
        try:
            gsheet.start_oauth(data)
            flash("a Google sign-in opened in your browser — finish it there")
        except gsheet.SheetError as exc:
            flash(str(exc), "error")
        return redirect(url_for("setup"))

    @app.get("/google/oauth/status")
    def google_oauth_status():
        return jsonify(gsheet.oauth_status(data))

    @app.post("/google/disconnect")
    def google_disconnect():
        gsheet.disconnect(data)
        flash("Google disconnected — the sheet itself is untouched")
        return redirect(url_for("setup"))

    @app.post("/sheet/create")
    def sheet_create():
        client = _client_or_flash()
        if client is None:
            return redirect(url_for("setup"))
        share_with = request.form.get("share_with", "").strip()
        try:
            sid, url = client.create(request.form.get("title", "").strip() or "Presence tracker",
                                     "Tracker")
            if share_with and gsheet.connection(data)["kind"] == "service_account":
                client.share(sid, share_with)
        except gsheet.SheetError as exc:
            flash(str(exc), "error")
            return redirect(url_for("setup"))
        _save_tracker(backend="sheet", sheet_id=sid, sheet_url=url, tab="Tracker",
                      share_with=share_with)
        flash("sheet created — press Sync now to fill it")
        return redirect(url_for("setup"))

    @app.post("/sheet/connect")
    def sheet_connect():
        sid = gsheet.sheet_id_from(request.form.get("sheet", ""))
        client = _client_or_flash()
        if not sid or client is None:
            if not sid:
                flash("paste the sheet's link or ID", "error")
            return redirect(url_for("setup"))
        try:
            url, tab = gsheet.connect_existing(client, sid)
        except gsheet.SheetError as exc:
            flash(str(exc), "error")
            return redirect(url_for("setup"))
        _save_tracker(backend="sheet", sheet_id=sid, sheet_url=url, tab=tab)
        flash("sheet connected — press Sync now")
        return redirect(url_for("setup"))

    @app.post("/sheet/sync")
    def sheet_sync():
        tr = read_yaml(data / "presence.yaml").get("tracker") or {}
        client = _client_or_flash()
        if not tr.get("sheet_id") or client is None:
            if not tr.get("sheet_id"):
                flash("create or connect a sheet first", "error")
            return redirect(url_for("setup"))
        tracker = Tracker(data / "tracker.db")
        try:
            res = gsheet.sync(tracker, client, tr["sheet_id"], tr.get("tab") or "Tracker",
                              data / gsheet.STATE_FILE)
        except gsheet.SheetError as exc:
            flash(str(exc), "error")
            return redirect(url_for("setup"))
        finally:
            tracker.close()
        flash("synced: " + res.summary())
        for issue in res.issues[:5]:
            flash(issue, "error")
        return redirect(url_for("setup"))

    @app.post("/sheet/import")
    def sheet_import():
        sid = gsheet.sheet_id_from(request.form.get("sheet", ""))
        client = _client_or_flash()
        if not sid or client is None:
            if not sid:
                flash("paste the link or ID of the sheet to import", "error")
            return redirect(url_for("setup"))
        try:
            _url, tabs = client.info(sid)
            rows = client.read(sid, gsheet.rng(tabs[0], "A1:Z")) if tabs else []
        except gsheet.SheetError as exc:
            flash(str(exc), "error")
            return redirect(url_for("setup"))
        if len(rows) < 2:
            flash("that sheet has no rows under its header", "error")
            return redirect(url_for("setup"))
        tracker = Tracker(data / "tracker.db")
        try:
            rep = import_rows(tracker, rows[0], rows[1:], year=date.today().year,
                              source_default="sheet-import")
        finally:
            tracker.close()
        flash("imported: " + rep.summary())
        for issue in rep.issues[:5]:
            flash(issue, "error")
        return redirect(url_for("tracker_page"))

    @app.post("/tracker/import")
    def tracker_import():
        f = request.files.get("csv")
        if f is None or not f.filename:
            flash("choose a CSV file", "error")
            return redirect(url_for("tracker_page"))
        rows = list(csv.reader(io.StringIO(f.read().decode("utf-8-sig", errors="replace"))))
        if len(rows) < 2:
            flash("that CSV has no rows under its header", "error")
            return redirect(url_for("tracker_page"))
        tracker = Tracker(data / "tracker.db")
        try:
            rep = import_rows(tracker, rows[0], rows[1:], year=date.today().year,
                              source_default="csv-import")
        finally:
            tracker.close()
        flash("imported: " + rep.summary())
        for issue in rep.issues[:5]:
            flash(issue, "error")
        return redirect(url_for("tracker_page"))

    @app.post("/setup/cv")
    def setup_cv():
        f = request.files.get("cv")
        if not f or not f.filename:
            flash("choose a PDF first", "error")
            return redirect(url_for("setup"))
        try:
            text = extract_text(f.read())
        except Exception as exc:
            flash(f"could not read that PDF: {type(exc).__name__}", "error")
            return redirect(url_for("setup"))
        fields = guess_fields(text)
        (data / "cv_draft.json").write_text(
            json.dumps({"fields": fields, "text_chars": fields.pop("text_chars")})
        )
        flash("CV read — check the fields below, then confirm")
        return redirect(url_for("setup"))

    @app.post("/setup/profile")
    def setup_profile():
        profile = read_yaml(data / "profile.yaml")
        profile["identity"] = {
            "name": request.form.get("name", "").strip(),
            "email": request.form.get("email", "").strip(),
        }
        profile["contact"] = {"phone": request.form.get("phone", "").strip()}
        profile["work_authorization"] = {
            "summary": request.form.get("work_auth", "").strip(),
            "needs_sponsorship": profile.get("work_authorization", {}).get("needs_sponsorship"),
        }
        profile["skills"] = _csv(request.form.get("skills", ""))
        profile["links"] = _csv(request.form.get("links", ""))
        profile["confirmed"] = request.form.get("confirmed") == "1" and bool(
            profile["identity"]["name"]
        )
        write_yaml(data / "profile.yaml", profile)
        flash(
            "profile confirmed — Presence may run"
            if profile["confirmed"]
            else "profile saved (not confirmed yet — nothing runs until you tick the box)"
        )
        return redirect(url_for("setup"))

    @app.post("/sources/add")
    def sources_add():
        url = request.form.get("url", "").strip()
        provider, board = request.form.get("provider", ""), request.form.get("board", "").strip()
        if url:
            hit = detect(url)
            if not hit:
                flash("that URL isn't a board on a published API Presence supports", "error")
                return redirect(url_for("setup"))
            provider, board = hit
        if provider not in {c["provider"] for c in available()} or not board:
            flash("pick a provider and a board token", "error")
            return redirect(url_for("setup"))
        label = request.form.get("label", "").strip() or board.replace("-", " ").title()
        cfg = read_yaml(data / "sources.yaml")
        sources = cfg.get("sources", [])
        sid = board.lower()
        if any(s.get("id") == sid for s in sources):
            sid = f"{sid}-{provider}"
        sources.append(
            {
                "id": sid,
                "provider": provider,
                "label": label,
                "enabled": True,
                "config": {"board": board},
            }
        )
        write_yaml(data / "sources.yaml", {"sources": sources})
        flash(f"added {label} ({provider})")
        return redirect(url_for("setup"))

    @app.post("/sources/<sid>/toggle")
    def sources_toggle(sid: str):
        cfg = read_yaml(data / "sources.yaml")
        for s in cfg.get("sources", []):
            if s.get("id") == sid:
                s["enabled"] = not s.get("enabled", True)
        write_yaml(data / "sources.yaml", cfg)
        return redirect(url_for("setup"))

    @app.post("/sources/<sid>/remove")
    def sources_remove(sid: str):
        cfg = read_yaml(data / "sources.yaml")
        cfg["sources"] = [s for s in cfg.get("sources", []) if s.get("id") != sid]
        write_yaml(data / "sources.yaml", cfg)
        return redirect(url_for("setup"))

    @app.post("/setup/filters")
    def setup_filters():
        cfg = read_yaml(data / "search.yaml")
        cfg.update(
            {
                "locations": _csv(request.form.get("locations", "")),
                "remote_ok": True,
                "title_include": _csv(request.form.get("title_include", "")),
                "title_exclude": _csv(request.form.get("title_exclude", "")),
                "freshness_hours": max(1, int(request.form.get("freshness_days") or 14)) * 24,
            }
        )
        cfg.setdefault("blocklist", [])
        write_yaml(data / "search.yaml", cfg)
        flash("filters saved")
        return redirect(url_for("setup"))

    @app.get("/tracker")
    def tracker_page():
        status = request.args.get("status") or None
        t = Tracker(data / "tracker.db")
        try:
            jobs = t.list(status)
            timelines = {j.id: t.timeline(j.id) for j in jobs}
        finally:
            t.close()
        return render_template(
            "tracker.html",
            page="tracker",
            jobs=jobs,
            timelines=timelines,
            statuses=STATUSES,
            status=status,
        )

    @app.post("/tracker/<int:job_id>/event")
    def tracker_event(job_id: int):
        t = Tracker(data / "tracker.db")
        try:
            t.record_event(job_id, request.form.get("kind", "note"), date.today())
            flash("recorded")
        except (ConventionError, KeyError) as exc:
            flash(str(exc), "error")
        finally:
            t.close()
        return redirect(url_for("tracker_page"))

    @app.get("/run")
    def run_page():
        last = data / "last_brief.txt"
        return render_template(
            "run.html", page="run", brief=last.read_text() if last.exists() else "", errors={}
        )

    @app.post("/run")
    def run_now():
        send = request.form.get("send") == "1"
        try:
            brief, errors, delivered = run_cycle(data, send=send)
        except (ConfigError, TelegramError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("run_page"))
        flash("cycle complete" + (" — brief sent to Telegram" if delivered else ""))
        return render_template("run.html", page="run", brief=brief, errors=errors)

    return app
