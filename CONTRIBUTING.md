# Contributing to Presence

Thanks for being here. Presence is pre-alpha; the most useful contributions
right now are issues that sharpen the design, and later, connectors and packs.

## Development setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Sofa3xpert/presence.git
cd presence
uv sync          # creates .venv and installs dev dependencies
uv run pytest    # tests
uv run ruff check .
```

**macOS gotcha — "No module named 'presence'" although `uv pip list` shows it:**
uv marks `.venv` with the macOS *hidden* flag; if the repo sits in an
iCloud-synced folder (Desktop/Documents), iCloud propagates that flag to every
file inside, and Python 3.12+ silently skips hidden `.pth` files
(astral-sh/uv#16977) — and keeps re-applying it from its cloud metadata, so
un-hiding alone doesn't stick. Fix: `uv run --no-sync python scripts/doctor.py`
— it marks `.venv` as ignored by the iCloud file provider
(`com.apple.fileprovider.ignore#P`) and un-hides everything. Better still,
**keep the repo outside iCloud-synced folders** (e.g. `~/dev/presence`).
Tests are immune either way — `tests/conftest.py` puts `src` on the path.

## Ground rules

- Read [CHARTER.md](CHARTER.md) first. PRs that weaken a charter rule will be
  declined regardless of technical quality.
- Conventional commits (`feat:`, `fix:`, `docs:`, `chore:`, ...); semver.
- One logical change per PR; tests accompany behavior changes.
- Connectors read only interfaces a provider publishes for that purpose
  (documented job-board APIs, the user's own mailbox). Scrapers of any kind —
  with or without evasion — are declined; see the catalog for what is on hold.

## Licensing of contributions

Code under `src/` is AGPL-3.0. Pack formats and their schemas under `packs/`
are MIT so shared community content stays frictionless. By contributing you
license your contribution under the file's governing license.
