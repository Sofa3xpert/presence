# Presence

**A job-search companion you run yourself.** Presence is an open, local-first
system that hunts for roles while you live your life: an AI Scout searches and
judges postings against *your* rules, a tracker keeps honest state, and a daily
brief lands in your messenger. You talk to it in plain words. It never applies
on your behalf — **a human clicks every Submit**.

Built by a graduate who needed it, for everyone in the same seat.

## Why it's free

Job seekers and students are exactly the people who can't spend $40/month on
"career copilots" — so everything essential in Presence is free, forever, with
no tiers and no subscription. It runs on your machine, with your own model key
— or fully free end-to-end with a local model via Ollama. If Presence helps
you land somewhere, there's a sponsor button. That's the whole business model.

## The charter

Four rules, enforced in code — see [CHARTER.md](CHARTER.md):

1. **No auto-submit, anywhere.** Presence finds and prepares; the human applies.
2. **No invented facts.** Nothing extracted about you is used until you confirm it.
3. **Your data stays on your machine.** No telemetry, no cloud account, no exceptions.
4. **Hard daily spend cap.** The system can never surprise you with a bill.

## Status: pre-alpha

Nothing runnable yet — this repository is the foundation commit of a system
being extracted from a private pipeline that has run in production since
August 2026 (6 agents, 7 connector types, 587 tracked applications).

**v1 scope** (see the architecture document, coming to `docs/`):
one source (LinkedIn), two agents (Scout + Brief), SQLite tracker,
Telegram delivery, BYO model key (Anthropic / OpenAI / Ollama), Docker,
conversational onboarding from an uploaded CV plus five questions.

## Layout

```
src/presence/
  core/        runtime: scheduler, agent executor, profile & rules, guards
  agents/      agent packs: Scout, Brief (versioned, user-editable prompts)
  connectors/  job sources behind one interface (v1: LinkedIn)
  adapters/    messenger transports (v1: Telegram)
  tracker/     SQLite tracker + conventions engine
  packs/       loaders for shareable packs (sims, drills — MIT-licensed formats)
packs/         the pack formats themselves (MIT)
docs/          documentation site source
```

## License

Core: [AGPL-3.0](LICENSE). Pack and SDK formats under `packs/`: MIT — so the
things the community shares stay frictionless.
