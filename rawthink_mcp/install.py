"""rawthink install — one-command setup for RAWThink MCP server.

Creates vault, configures MCP in Claude Code, installs SessionStart hook,
and copies starter files. Idempotent — safe to run multiple times.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _check_prereqs() -> dict[str, bool]:
    """Check for required tools. Returns dict of tool -> available."""
    checks = {}

    # Claude Code
    claude_json = Path.home() / ".claude.json"
    checks["claude_code"] = claude_json.exists()

    # Docker
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        checks["docker"] = True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        checks["docker"] = False

    # Ollama
    try:
        subprocess.run(
            ["ollama", "--version"],
            capture_output=True,
            timeout=10,
        )
        checks["ollama"] = True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        checks["ollama"] = False

    # Node.js
    try:
        subprocess.run(
            ["node", "--version"],
            capture_output=True,
            timeout=10,
        )
        checks["node"] = True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        checks["node"] = False

    return checks


def _create_vault(vault_path: Path) -> None:
    """Create vault directory structure."""
    dirs = [
        vault_path / "sessions",
        vault_path / "qnotes",
        vault_path / "archive" / "raw",
        vault_path / "outputs",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    # Create empty memory.jsonl if it doesn't exist
    memory_file = vault_path / "memory.jsonl"
    if not memory_file.exists():
        memory_file.touch()


def _configure_mcp(vault_path: Path) -> None:
    """Add rawthink server to ~/.claude.json MCP config."""
    claude_json = Path.home() / ".claude.json"

    config = {}
    if claude_json.exists():
        try:
            config = json.loads(claude_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            config = {}

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    # Use forward slashes in vault path for JSON (cross-platform)
    vault_str = str(vault_path).replace("\\", "/")

    config["mcpServers"]["rawthink"] = {
        "command": "rawthink-mcp",
        "env": {
            "RAWTHINK_VAULT": vault_str,
        },
    }

    claude_json.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _install_hook(vault_path: Path) -> None:
    """Copy handoff hook and register it in Claude Code settings."""
    hooks_dir = Path.home() / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # Copy rawthink-handoff.js from package
    src = Path(__file__).parent / "rawthink-handoff.js"
    dst = hooks_dir / "rawthink-handoff.js"
    shutil.copy2(src, dst)

    # Register in settings.json
    settings_path = Path.home() / ".claude" / "settings.json"
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            settings = {}

    if "hooks" not in settings:
        settings["hooks"] = {}
    if "SessionStart" not in settings["hooks"]:
        settings["hooks"]["SessionStart"] = []

    vault_str = str(vault_path).replace("\\", "/")
    hook_script = str(dst).replace("\\", "/")
    hook_command = f"node {hook_script} {vault_str}"

    # Check if already registered (idempotent)
    # Claude Code format: SessionStart is array of {matcher?, hooks: [{type, command}]}
    existing = settings["hooks"]["SessionStart"]
    already = any(
        isinstance(entry, dict)
        and any(
            "rawthink-handoff" in h.get("command", "")
            for h in entry.get("hooks", [])
            if isinstance(h, dict)
        )
        for entry in existing
    )

    if not already:
        existing.append({
            "hooks": [
                {
                    "type": "command",
                    "command": hook_command,
                }
            ],
        })

    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _copy_starter_files() -> None:
    """Copy CLAUDE.md and THINKING_DIRECTIVES.md to CWD if they don't exist."""
    cwd = Path.cwd()
    package_dir = Path(__file__).parent.parent  # repo root when dev, won't exist when installed

    for filename in ("CLAUDE.md", "THINKING_DIRECTIVES.md"):
        target = cwd / filename
        if target.exists():
            print(f"  {filename} already exists — skipping")
            continue

        # Try repo root first (development install)
        source = package_dir / filename
        if source.exists():
            shutil.copy2(source, target)
            print(f"  Copied {filename}")
            continue

        # Fallback: generate minimal starter content
        if filename == "CLAUDE.md":
            target.write_text(_CLAUDE_MD_TEMPLATE, encoding="utf-8")
            print(f"  Created {filename}")
        elif filename == "THINKING_DIRECTIVES.md":
            target.write_text(_THINKING_DIRECTIVES_TEMPLATE, encoding="utf-8")
            print(f"  Created {filename}")


# ---------------------------------------------------------------------------
# Embedded templates (used when package files aren't available)
# ---------------------------------------------------------------------------

_CLAUDE_MD_TEMPLATE = """\
# RAWThink — System Directives

> These are default directives. Customize them to match your thinking style.
> See README.md for details on each directive.

## Role

You are a thinking companion — philosopher peer. Equal level,
give pushback when something seems wrong, don't parrot.

## Tone

Philosopher peer. Push back politely but clearly on wrong assertions.
You're not an authority — you're a thinking partner.

## Thinking Directives

Read and apply `THINKING_DIRECTIVES.md` at session start.

## Epistemic Transparency (end of each response)

```
ASSUMPTIONS: [bullet points]
SPECULATIVE vs GROUNDED: [which parts are speculative, which empirical/mathematical]
GAPS: [open questions / missing info]
```

## Active Modes

- **Free-flow**: User talks, you listen and respond
- **Socratic**: Ask deepening questions
- **Debate**: Generate counter-arguments — pushback
- **Synthesis**: Build connections, draw maps
- **Deep-dive**: Trace concepts to their roots
- **Technical**: Use math and physics
- **Galaxy-brain**: Creative, wide imagination — no limits

## MCP-First Rule

**Always use rawthink MCP tools to access vault and session data.**
`get_session`, `search_thoughts`, `get_related`, `open_nodes`, `search_nodes` —
these exist for this purpose. Never parse raw JSONL files or read transcripts manually.

## Persist Memory — Layered Access

### Session Start
1. Auto memory (MEMORY.md) loads automatically — hot cache
2. When a topic comes up, use `rawthink` MCP to find related content
3. For deep context, use `search_thoughts` for semantic vault search
4. Scan previous session's qnotes for unresolved action items

### During Session
- On new thoughts, use `search_thoughts` to find related past thoughts
- On confirmed decisions/rules, use knowledge graph tools to persist
- When storing qnotes with `store_thought`, ALWAYS pass `session_id`

### Session End
- Key insights → knowledge graph entities + observations
- Summary if needed → update auto memory topic files
"""

_THINKING_DIRECTIVES_TEMPLATE = """\
# Thinking Directives

> Rules for the thinking partnership between human and AI.
> Not restrictions — structure. Not a checklist — a discipline.
> Every rule here was learned by failing at it. Customize and extend as needed.

---

## 1. Verify Before Stating

Don't say it unless you've checked it. If you're about to state something as fact,
ask: did I verify this, or am I filling a slot in my response?

- If you know → state it with source
- If you're inferring → say "I think X because Y"
- If you don't know → say so and go find out
- Never manufacture a claim to make an analysis feel complete

---

## 2. Name Things Precisely

If you can't name it correctly, you don't understand it well enough to discuss it.

---

## 3. Real Epistemic Transparency

The template is not the practice. Writing `ASSUMPTIONS: [none]` is not transparency.
Real transparency is woven into the response itself.

---

## 4. Corrections Must Transform

Accepting a correction without understanding it is worse than arguing against it.
When corrected: articulate what was wrong, why, and what changes.

---

## 5. Distinguish Layers

When two things share a word, they're probably different things.
List the layers explicitly before discussing.

---

## 6. Speed ≠ Value

A fast wrong answer wastes more time than a slow right one.

---

## 7. The Anti-Mechanical Rule

If any directive in this document starts being followed mechanically, it has failed.

---

## Open End

This document is incomplete by design. New failure modes will emerge.
Add rules born from your own failures. The directives evolve.
"""


def main():
    parser = argparse.ArgumentParser(
        description="Set up RAWThink MCP for Claude Code",
    )
    parser.add_argument(
        "--vault",
        type=str,
        default=str(Path.home() / "rawthink-vault"),
        help="Path to vault directory (default: ~/rawthink-vault)",
    )
    args = parser.parse_args()
    vault_path = Path(args.vault).resolve()

    print("RAWThink Install\n")

    # Step 1: Check prerequisites
    print("Checking prerequisites...")
    prereqs = _check_prereqs()

    if not prereqs["claude_code"]:
        print("  WARNING: ~/.claude.json not found — is Claude Code installed?")
    else:
        print("  Claude Code: found")

    if not prereqs["docker"]:
        print("  WARNING: Docker not running — needed for Qdrant")
    else:
        print("  Docker: running")

    if not prereqs["ollama"]:
        print("  WARNING: Ollama not found — needed for embeddings")
    else:
        print("  Ollama: found")

    if not prereqs["node"]:
        print("  WARNING: Node.js not found — needed for SessionStart hook")
    else:
        print("  Node.js: found")

    missing = [k for k, v in prereqs.items() if not v]
    if missing:
        print(f"\n  Missing: {', '.join(missing)}")
        print("  Install missing prerequisites before using RAWThink.")
        print("  Continuing setup anyway...\n")
    else:
        print()

    # Step 2: Create vault
    print(f"Creating vault: {vault_path}")
    _create_vault(vault_path)
    print(f"  Vault ready: {vault_path}")

    # Step 3: Configure MCP
    print("\nConfiguring MCP in ~/.claude.json...")
    _configure_mcp(vault_path)
    print("  MCP configured")

    # Step 4: Install hook
    print("\nInstalling SessionStart hook...")
    _install_hook(vault_path)
    print("  Hook installed")

    # Step 5: Copy starter files
    print("\nCopying starter files to current directory...")
    _copy_starter_files()

    # Step 6: Summary
    vault_str = str(vault_path).replace("\\", "/")
    print(f"""
Setup complete.

  Vault:     {vault_str}
  MCP:       ~/.claude.json updated
  Hook:      ~/.claude/hooks/rawthink-handoff.js
  Templates: CLAUDE.md, THINKING_DIRECTIVES.md

Your vault is empty — that's the point. Start a conversation
in Claude Code, use store_thought to save your first insight,
and run /rtclose when you're done. Your memory grows from here.

Next steps:
  docker compose up -d    # start Qdrant
  ollama pull bge-m3      # download embedding model
  # restart Claude Code
""")


if __name__ == "__main__":
    main()
