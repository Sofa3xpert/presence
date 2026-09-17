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

Tests never touch the network; `tests/conftest.py` puts `src` on the path, so
they run against the working tree whether or not the package is installed.

## Ground rules

- Read [CHARTER.md](CHARTER.md) first. PRs that weaken a charter rule will be
  declined regardless of technical quality.
- **No scraping.** Connectors read only interfaces a provider publishes for
  that purpose (documented job-board APIs, the person's own mailbox). Scrapers
  of any kind — with or without evasion — are declined; see the catalog for
  what is on hold.
- **No code path that submits.** Presence finds and prepares; a human clicks
  every Submit. Nothing that fills a form or posts on a site on the person's
  behalf will be merged.
- Conventional commits (`feat:`, `fix:`, `docs:`, `chore:`, ...); semver.
- One logical change per PR; tests accompany behavior changes.

## Licensing of contributions

Code under `src/` is AGPL-3.0. Pack formats and their schemas under `packs/`
are MIT so shared community content stays frictionless. By contributing you
license your contribution under the file's governing license.
