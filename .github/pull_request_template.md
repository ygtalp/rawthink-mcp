## What this changes

## Why

The reasoning, not just the behaviour. If this fixes a defect, describe how the
defect was silent — most of the ones found in this codebase were.

## Verification

- [ ] `pytest` green
- [ ] `ruff check --select F,E9 rawthink_mcp tests` clean
- [ ] `python scripts/check_templates.py` in sync (if `install.py` or any
      embedded document changed)
- [ ] New behaviour has a test that **fails without the change** — reverted the
      change and watched it go red

## Schema changes

If this touches the vocabulary or the graph format, `config.py`, `migrate.py`
and the session-close instructions are three views of one contract. They drift
apart quietly when they are not edited together.
