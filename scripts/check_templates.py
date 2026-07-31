#!/usr/bin/env python3
"""Fail if an install template has drifted from its repo file.

install.py embeds four documents so `rawthink-install` works offline. Embedded
copies drift silently — one of them had fallen 65 lines behind before anyone
noticed, which meant installed users and repo readers were getting different
documents. This check makes that loud.

    python scripts/check_templates.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from rawthink_mcp import install as I  # noqa: E402

PAIRS = [
    ("CLAUDE.md", I._CLAUDE_MD_TEMPLATE),
    ("THINKING_DIRECTIVES.md", I._THINKING_DIRECTIVES_TEMPLATE),
    ("SETUP.md", I._SETUP_MD_TEMPLATE),
    ("docker-compose.yml", I._DOCKER_COMPOSE_TEMPLATE),
    (".claude/commands/rtclose.md", I._RTCLOSE_MD_TEMPLATE),
]


def norm(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def main() -> int:
    drift = []
    for name, template in PAIRS:
        path = ROOT / name
        if not path.exists():
            print(f"  ?  {name}: no repo file to compare against")
            continue
        a, b = norm(path.read_text(encoding="utf-8")), norm(template)
        if a == b:
            print(f"  ok {name}")
        else:
            print(f"  DRIFT {name}: repo {len(a.splitlines())} lines, "
                  f"template {len(b.splitlines())} lines")
            drift.append(name)
    if drift:
        print(f"\n{len(drift)} template(s) out of sync with the repo. "
              f"Installed users would get different content than the repo shows.")
        return 1
    print("\nall templates in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
