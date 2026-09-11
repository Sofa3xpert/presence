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
Python 3.12+ silently skips `.pth` files that carry the macOS *hidden* flag,
and some uv builds write the editable install's `.pth` that way. Run
`uv run --no-sync python scripts/doctor.py` — it finds and un-hides the file.
Tests are immune (`tests/conftest.py` puts `src` on the path directly).

## Ground rules

- Read [CHARTER.md](CHARTER.md) first. PRs that weaken a charter rule will be
  declined regardless of technical quality.
- Conventional commits (`feat:`, `fix:`, `docs:`, `chore:`, ...); semver.
- One logical change per PR; tests accompany behavior changes.
- Connectors must use official/public endpoints and respect the source's
  terms; scrapers that require evasion belong nowhere in this project.

## Licensing of contributions

Code under `src/` is AGPL-3.0. Pack formats and their schemas under `packs/`
are MIT so shared community content stays frictionless. By contributing you
license your contribution under the file's governing license.
