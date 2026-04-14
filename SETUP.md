# RAWThink Setup Guide

> This file is read by Claude Code after `pip install rawthink-mcp`.
> Claude follows these steps to set up the infrastructure for the user.
> The user should NOT need to read this — Claude does.

---

## Prerequisites Check

Before starting, verify:

```bash
# Docker installed and running?
docker --version
docker info > /dev/null 2>&1

# Ollama installed?
ollama --version

# Node.js installed? (for SessionStart hook)
node --version

# Python 3.10+?
python --version
```

If any prerequisite is missing, tell the user what to install and where to get it:
- Docker: https://docs.docker.com/get-docker/
- Ollama: https://ollama.com/download
- Node.js: https://nodejs.org/
- Python 3.10+: https://www.python.org/downloads/ or `uv python install 3.13`

**Do not proceed until prerequisites are confirmed.**

---

## Step 1: Start Qdrant

Qdrant is the vector database for semantic search. It runs as a Docker container.

```bash
# If the user cloned the repo (docker-compose.yml exists):
docker compose up -d

# If installed via pip (no docker-compose.yml):
docker run -d --name rawthink-qdrant \
  -p 6333:6333 \
  -v rawthink_qdrant_data:/qdrant/storage \
  --restart unless-stopped \
  qdrant/qdrant:latest
```

**Verify:** `curl -s http://localhost:6333/healthz` should return OK or `{"title":"qdrant..."}`.

If port 6333 is taken, use a different port and tell the user to set `QDRANT_URL=http://localhost:<port>` in their MCP config env.

---

## Step 2: Pull Embedding Model

BGE-M3 is the multilingual embedding model used for semantic search (1024-dim dense vectors).

```bash
ollama pull bge-m3
```

This downloads ~1.2GB. Wait for completion.

**Verify:** `ollama list` should show `bge-m3`.

If Ollama is running but pull fails, check: `ollama serve` might need to be started first.

---

## Step 3: Initialize Vault

The vault is where sessions, qnotes, and knowledge graph data live.

```bash
# Choose a location for the vault
VAULT_DIR="$HOME/rawthink-vault"  # or wherever the user wants

mkdir -p "$VAULT_DIR"/{sessions,qnotes,archive/raw,outputs}
touch "$VAULT_DIR/memory.jsonl"
```

Ask the user where they want their vault. Suggest `~/rawthink-vault` as default.

---

## Step 4: Configure MCP

Add to the user's `~/.claude.json`:

```json
{
  "mcpServers": {
    "rawthink": {
      "command": "rawthink-mcp",
      "env": {
        "RAWTHINK_VAULT": "<VAULT_DIR from step 3>",
        "MEMORY_FILE_PATH": "<VAULT_DIR>/memory.jsonl"
      }
    }
  }
}
```

**Windows note:** Use forward slashes in paths (`C:/Users/name/rawthink-vault`).

---

## Step 5: Copy Starter Files

Copy CLAUDE.md and THINKING_DIRECTIVES.md to the user's project:

```bash
# If cloned from repo, these already exist
# If installed via pip, download from GitHub:
curl -sL https://raw.githubusercontent.com/ygtalp/rawthink-mcp/main/CLAUDE.md -o CLAUDE.md
curl -sL https://raw.githubusercontent.com/ygtalp/rawthink-mcp/main/THINKING_DIRECTIVES.md -o THINKING_DIRECTIVES.md
```

Tell the user to customize CLAUDE.md — role, tone, modes, language preferences.

---

## Step 6: Session Handoff Hook

The handoff hook ensures session context carries over between conversations. It runs automatically at each session start, reading the previous session's handoff and listing recent qnotes.

**1. Copy the hook script:**

```bash
mkdir -p ~/.claude/hooks

# If cloned from repo:
cp rawthink_mcp/rawthink-handoff.js ~/.claude/hooks/

# If installed via pip, download from GitHub:
curl -sL https://raw.githubusercontent.com/ygtalp/rawthink-mcp/main/rawthink_mcp/rawthink-handoff.js -o ~/.claude/hooks/rawthink-handoff.js
```

**2. Add to `~/.claude/settings.json`:**

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node ~/.claude/hooks/rawthink-handoff.js <VAULT_DIR from step 3>"
          }
        ]
      }
    ]
  }
}
```

Replace `<VAULT_DIR from step 3>` with the actual vault path (e.g. `~/rawthink-vault`).

**Windows note:** Use the full path: `node C:/Users/<name>/.claude/hooks/rawthink-handoff.js C:/Users/<name>/rawthink-vault`

**What this does:**
- Reads `vault/handoff-*.md` files and injects them into Claude's context
- Lists recent qnotes (last 7 days) so Claude can scan for unresolved action items
- Runs automatically — no manual steps needed after setup

**Verify:** Start a new Claude Code session. You should see "RAWThink Session Handoff" in the startup output (after running `/rtclose` at least once).

---

## Step 7: Verify

Restart Claude Code, then test:

1. Ask Claude to run `search_thoughts` with any query — should return "No results found." (empty vault is OK)
2. Ask Claude to run `store_thought` with a test thought — should create a qnote
3. Ask Claude to run `search_thoughts` again — should find the test thought
4. Ask Claude to run `read_graph` — should return empty graph

If any step fails:
- **Qdrant connection error** → check Docker is running, port 6333 accessible
- **Ollama embedding error** → check `ollama list` shows bge-m3, `ollama serve` is running
- **Vault not found** → check RAWTHINK_VAULT env var path exists

---

## Step 8: First Session

The vault is empty. Start using it:

1. Have a conversation with Claude about any topic
2. At the end, Claude can use `store_thought` to save key insights
3. Use `create_entities` to build the knowledge graph
4. Next session, `search_thoughts` will find previous context

Over time, the three memory layers fill:
- **MEMORY.md** — manually curated hot cache
- **Knowledge Graph** (memory.jsonl) — entities, relations, observations
- **Semantic Search** (Qdrant) — full-text across all sessions and qnotes

---

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| `spawn rawthink-mcp ENOENT` | rawthink-mcp not in PATH | Use absolute path, or check `pip show rawthink-mcp` for install location |
| `Ollama unavailable` | ollama not running | `ollama serve` in a terminal |
| `Qdrant connection refused` | Docker not running or wrong port | `docker ps` to check, `docker compose up -d` |
| `No results` after indexing | BM25 state stale | Run `reindex` with `full=true` |
| Turkish chars not matching | Normalization off | Set env `RAWTHINK_TURKISH_NORMALIZATION=1` in MCP config |
