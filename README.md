# RAWThink

**Your AI assistant forgets everything between sessions. RAWThink fixes that.**

> Persistent memory for Claude Code — hybrid search, knowledge graph, session lifecycle.
> Think of it as a second brain that grows with every conversation.

## The Problem

Claude Code is stateless. Every session starts from zero. You explain the same context, re-establish the same decisions, re-discover the same insights. Yesterday's breakthrough is today's cold start.

RAWThink gives your AI a memory that persists, decays naturally, and revises itself when you change your mind. It's a 15-tool MCP server that turns Claude Code into a long-term thinking partner.

## Article

Read the full story: [Your AI Doesn't Remember You] ([https://dev.to/yigitaunal](https://dev.to/yigitaunal/your-ai-doesnt-remember-you-thats-about-to-matter-more-than-you-think-77k))

## Quick Start

### Prerequisites

- Python 3.10+
- [Docker](https://docs.docker.com/get-docker/) (for Qdrant vector database)
- [Ollama](https://ollama.ai/) (for local embeddings)
- [Node.js](https://nodejs.org/) (for the SessionStart hook)

### Install & set up

```bash
pip install rawthink-mcp
rawthink-install              # creates vault, configures MCP, installs hook
docker run -d --name rawthink-qdrant -p 6333:6333 -v rawthink_qdrant:/qdrant/storage qdrant/qdrant
ollama pull bge-m3            # download embedding model
```

> If you cloned the repo, you can use `docker compose up -d` instead.

Restart Claude Code. Done.

`rawthink-install` handles everything: vault directory, `~/.claude.json` MCP config, SessionStart hook, and starter files (CLAUDE.md, THINKING_DIRECTIVES.md). Run `rawthink-install --vault ~/my-vault` to customize the vault location.

<details>
<summary>Manual setup (without rawthink-install)</summary>

Add to your `~/.claude.json`:

```json
{
  "mcpServers": {
    "rawthink": {
      "command": "rawthink-mcp",
      "env": {
        "RAWTHINK_VAULT": "/path/to/your/vault"
      }
    }
  }
}
```

Create vault structure:

```bash
mkdir -p vault/{sessions,qnotes,archive/raw,outputs}
```

Install from source instead of PyPI:

```bash
git clone https://github.com/ygtalp/rawthink-mcp.git
cd rawthink-mcp
pip install -e .
```

</details>

## Your First Session

Your vault starts empty — that's the point. Open Claude Code and start talking:

```
You:   "We're building a REST API. Let's use PostgreSQL with connection pooling."
Claude: [responds with technical discussion]

You:   "/qnote we decided on PostgreSQL + pgBouncer for the API layer"
       → saved to your vault as a searchable quick note

You:   "/rtclose"
       → Session exported, entities extracted, handoff written
```

Next session, Claude loads the handoff automatically and picks up where you left off. After a few sessions, your vault looks like this:

```
> search_thoughts("how did we handle the database?")

1. [2025-03-15_002] API Architecture Decisions        score: 0.74
   Decided on PostgreSQL + pgBouncer for connection pooling.
   Key insight: pool_mode=transaction for serverless...

2. [2025-03-22_001] Performance Review                 score: 0.61
   Revisited DB strategy — added read replicas for
   reporting queries, write primary stays single-node...

> read_graph(summary=True)

Knowledge Graph: 42 entities, 38 relations

  decision (5)
  - postgres-connection-pooling (3 obs) act=0.97
  - api-versioning-strategy    (4 obs) act=0.85
  - auth-jwt-vs-session        (2 obs) act=0.72
  concept (8)
  - cqrs-pattern               (3 obs) act=0.91
  - event-sourcing             (5 obs) act=0.88
  ...
```

## What It Does

### Three-layer memory

1. **Hot cache** (MEMORY.md) — loaded every session, essential context
2. **Knowledge graph** (JSONL) — entities, relations, temporal observations with belief revision
3. **Semantic search** (Qdrant) — hybrid dense + BM25 across all sessions and qnotes

### Session lifecycle

```
Session start (handoff loads) → Think together → /rtclose →
JSONL export + entity extraction + handoff for next session
```

### Key capabilities

- **Hybrid search**: BGE-M3 dense embeddings + BM25 sparse vectors, fused with RRF
- **Belief revision**: observations can be invalidated, superseded, and tracked over time
- **Activation decay**: unused knowledge fades (~23-day half-life), accessed knowledge stays hot
- **Epistemic typing**: mark entities as `assertion`, `hypothesis`, or `speculation`
- **Multi-terminal safe**: parallel sessions don't overwrite each other's handoffs
- **Thinking directives**: structured discipline for human-AI thinking partnerships
- **Ollama fallback**: embedding cache for when Ollama is temporarily unavailable

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Claude Code                    │
│              (MCP Client)                       │
└──────────────────┬──────────────────────────────┘
                   │ MCP (stdio)
┌──────────────────▼──────────────────────────────┐
│                rawthink                         │
│           (FastMCP Server)                      │
│                                                 │
│  ┌─────────────┐  ┌──────────┐  ┌────────────┐  │
│  │   Indexer   │  │  Graph   │  │  Chunker   │  │
│  │ (Qdrant +   │  │ (JSONL   │  │ (Markdown  │  │
│  │  Ollama)    │  │  KG)     │  │  splitter) │  │
│  └──────┬──────┘  └────┬─────┘  └────────────┘  │
│         │              │                        │
│  ┌──────▼──────┐  ┌────▼─────┐                  │
│  │   Qdrant    │  │ memory   │                  │
│  │  (Docker)   │  │ .jsonl   │                  │
│  └─────────────┘  └──────────┘                  │
└─────────────────────────────────────────────────┘
                   │
        ┌──────────▼──────────┐
        │       Vault         │
        │  sessions/ qnotes/  │
        │  archive/  outputs/ │
        └─────────────────────┘
```

## MCP Tools

### Search (5 tools)

| Tool              | Description                                   |
| ----------------- | --------------------------------------------- |
| `search_thoughts` | Hybrid semantic + keyword search across vault |
| `get_session`     | Retrieve full session content by ID           |
| `get_related`     | Quick related thoughts lookup (session-level) |
| `store_thought`   | Store a new thought as a qnote                |
| `reindex`         | Re-index vault content into Qdrant            |

### Knowledge Graph (10 tools)

| Tool                      | Description                                        |
| ------------------------- | -------------------------------------------------- |
| `search_nodes`            | Search entities by name, type, and observations    |
| `create_entities`         | Create/merge entities with epistemic typing        |
| `create_relations`        | Create relations (controlled vocabulary)           |
| `add_observations`        | Add temporal observations to entities              |
| `invalidate_observations` | Mark observations as invalidated (belief revision) |
| `delete_entities`         | Delete entities and their relations                |
| `delete_observations`     | Remove specific observations                       |
| `delete_relations`        | Remove specific relations                          |
| `read_graph`              | Read full graph with pagination + summary mode     |
| `open_nodes`              | Open specific entities with their relations        |

## How It's Different

RAWThink is not a codebase analyzer or a simple key-value memory. It's a living knowledge system designed for thinking partnerships.

|                    | RAWThink                         | Basic MCP Memory | Codebase Analyzers           |
| ------------------ | -------------------------------- | ---------------- | ---------------------------- |
| Search             | Hybrid (dense + BM25 + RRF)      | Key lookup       | Graph query                  |
| Knowledge          | Temporal KG with belief revision | Flat key-value   | Static AST graph             |
| Memory decay       | Activation decay (~23 days)      | None             | None                         |
| Session lifecycle  | Full (export, extract, handoff)  | None             | None                         |
| Epistemic tracking | assertion/hypothesis/speculation | None             | extracted/inferred/ambiguous |
| Designed for       | Thinking & decision memory       | Simple facts     | Code structure               |

## Why JSONL?

Human-readable, git-diffable, zero dependency. At 120 entities the graph loads in <50ms. At 10K entities ~1.4s. The optimization path is clear: mtime cache for repeated reads, lazy writes for activation updates, SQLite migration at 50K+ entities.

## Configuration

All settings via environment variables or `rawthink_mcp/config.py`:

| Setting               | Env Var                          | Default                  | Description                          |
| --------------------- | -------------------------------- | ------------------------ | ------------------------------------ |
| Vault path            | `RAWTHINK_VAULT`                 | `../vault`               | Path to vault directory              |
| Qdrant URL            | `QDRANT_URL`                     | `http://localhost:6333`  | Qdrant server URL                    |
| Qdrant path           | `QDRANT_PATH`                    | —                        | Set for embedded Qdrant (no Docker)  |
| Ollama URL            | `OLLAMA_URL`                     | `http://localhost:11434` | Ollama server URL                    |
| Embedding model       | `OLLAMA_MODEL`                   | `bge-m3`                 | Ollama embedding model               |
| KG file               | `MEMORY_FILE_PATH`               | `../vault/memory.jsonl`  | Knowledge graph file path            |
| Turkish normalization | `RAWTHINK_TURKISH_NORMALIZATION` | `false`                  | Set `1` or `true` to enable          |
| Decay rate            | —                                | `0.03`                   | Activation decay (~23-day half-life) |

## Session Lifecycle

RAWThink includes a session close command (`/rtclose`) that:

1. **Exports** the current conversation to clean markdown + full transcript + HTML
2. **Extracts** key entities and relations into the knowledge graph
3. **Writes** a project-specific handoff file for the next session
4. **Updates** MEMORY.md with essential context

A SessionStart hook (`rawthink-handoff.js`) runs when Claude Code starts and loads the most recent handoff + recent qnotes into context automatically.

> Session lifecycle currently requires Claude Code. MCP-native session tools are planned for v0.2.

## Customization

### CLAUDE.md

Edit `CLAUDE.md` to customize the thinking companion's behavior:

- Role and tone
- Active modes (free-flow, socratic, debate, synthesis, deep-dive, technical, galaxy-brain)
- Epistemic transparency format
- Meta-commands

### THINKING_DIRECTIVES.md

Structured discipline for the thinking partnership. Every rule was born from a violation — don't follow mechanically, understand why each exists. Customize to match your workflow.

### Obsidian (optional)

Open your vault directory in Obsidian to browse sessions and qnotes visually. The vault uses standard markdown with YAML frontmatter — no plugins needed.

### Entity Types

Default entity type is `"concept"`. Common types: `concept`, `decision`, `rule`, `insight`, `question`, `project`.

### Relation Types

Canonical types (non-standard accepted with warning):
`supports`, `contradicts`, `evolved_into`, `depends_on`, `exemplifies`, `part_of`, `caused_by`, `enables`, `supersedes`, `related_to`

## Roadmap

- Interactive knowledge graph visualization
- Wiki export (agent-browsable markdown per entity)
- `.rawthinkignore` for vault indexing control
- Graph traversal queries (unexpected connections, multi-hop)
- SQLite backend for 50K+ entities
- MCP-native session tools (no Claude Code dependency)
- Configurable language normalization



## License

MIT — see [LICENSE](LICENSE).
