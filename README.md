# RAWThink

Persistent memory for AI thinking partnerships. A knowledge graph you can argue
with, search that spans every session you have ever had, and a record of not
just what you decided but what you rejected.

MCP server for Claude Code. Python, local, no cloud.

---

## The problem

Every conversation with an AI starts from nothing. You explain the same context,
re-derive the same conclusions, and rediscover decisions you already made — and
the reasoning that produced them is gone the moment the window scrolls.

Chat history does not fix this. History is a transcript; what you need is
*structure*: which ideas connect, which beliefs you have since abandoned, which
alternatives you considered and dropped, and why.

RAWThink keeps that structure in three layers, in files you own.

## What it does

**Hybrid semantic search** across every session and note — BGE-M3 dense
embeddings and BM25 sparse vectors, fused with RRF. Ask "what did I think about
free will?" and get the passages, not a keyword match.

**A temporal knowledge graph** where observations carry dates and status.
Beliefs can be marked invalidated and linked to what replaced them, so the
archive remembers not only what you think but what you used to think.

**Session lifecycle** — a close command that exports the conversation, extracts
entities into the graph, and writes a handoff the next session loads
automatically.

**Activation decay** — unused knowledge fades on a ~23-day half-life, accessed
knowledge stays warm. Old material is still there; it just stops crowding out
what you are working on now.

---

## Quick start

### Prerequisites

- Python 3.10+
- Docker for Qdrant — or set `QDRANT_PATH` for embedded mode
- [Ollama](https://ollama.com) with `bge-m3`: `ollama pull bge-m3`

### Install

```bash
pip install rawthink-mcp
rawthink-install                 # creates ~/rawthink-vault with everything inside
cd ~/rawthink-vault
docker compose up -d             # starts Qdrant
```

`rawthink-install` writes the vault structure, `CLAUDE.md`,
`THINKING_DIRECTIVES.md`, `SETUP.md`, `docker-compose.yml` and the `/rtclose`
command. Use `rawthink-install --vault ~/my-vault` for a different location.

### Register with Claude Code

```bash
claude mcp add --scope user rawthink -- rawthink-mcp
```

### Upgrading from 0.x

**1.5.0 changes the graph schema.** Migrate before writing anything:

```bash
python -m rawthink_mcp.migrate --path vault/memory.jsonl --guess-domains --heal-dangling
```

That is a dry run — it prints what would change and writes nothing. Read the
report, then re-run with `--apply`. A timestamped backup is taken first.

See [CHANGELOG.md](CHANGELOG.md) for what changed and why.

---

## Your first session

```
> search_thoughts("what have I decided about caching?")

> record_decision(
    name="api/cache: read-through",
    domain="software",
    decided="read-through cache in front of the read model",
    because="the write path is already the bottleneck; adding invalidation there costs more",
    rejected=["write-through — couples the write path to cache health",
              "no cache — p99 was 400ms against a 200ms SLO"]
  )
```

Close with `/rtclose`. It exports the conversation, extracts what is worth
keeping into the graph, and leaves a handoff for next time — which the next
session loads on its own.

---

## The schema, and why it looks like this

This is the part worth understanding, because it is what keeps the graph
queryable over years rather than months.

### Role and subject are separate fields

`entityType` answers **what role does this node play**. Closed list of ten:

| type | for |
|---|---|
| `decision` | a choice made, with alternatives rejected |
| `concept` | an idea, theory, model, analogy |
| `finding` | something discovered or measured — a bug, a result, an audit |
| `rule` | a durable constraint or pattern to follow |
| `open-question` | unresolved, waiting on evidence |
| `artifact` | a project, tool, document, feature, source |
| `insight` | a realisation that changed how something is seen |
| `task` | a unit of intended work |
| `event` | something that happened at a point in time |
| `thing` | a person, object or substance named directly |

`domain` answers **what subject is it about**: `software`, `music`, `history`,
`philosophy`, `health`, `writing`, `neuro`, `finance`, `personal`, `galaxy`.

Keeping these apart is not tidiness. When one field carries both, the type list
grows by one entry per subject — a real vault reached 46 types this way, with
`saglik-bulgusu`, `teknik-karar` and `bug-pattern` sitting next to `karar`. At
that point nothing can be filtered, because no two entries agree on what a type
means.

### Unknown relation types are rejected, not warned about

Canonical vocabulary: `supports`, `contradicts`, `evolved_into`, `depends_on`,
`exemplifies`, `part_of`, `caused_by`, `enables`, `supersedes`, `related_to`,
`investigates`, `informs`, `uses`.

Close synonyms fold automatically — `connected_to` → `related_to`, `aspect_of` →
`part_of`. Anything else raises.

An earlier version accepted unknown types with a warning. Nothing acted on the
warning and 56 one-off types accumulated. **A warning that lets the write
through is a decision to allow it, written in the voice of disapproval.**

### Epistemic status defaults to unknown

`assertion`, `hypothesis`, `speculation` — or `unknown` when unstated.

`unknown` is deliberate. If a session did not establish something, recording it
as an assertion promotes a claim nobody made. The migration follows the same
rule: 144 entities with no epistemic field became `unknown`, not `assertion`.

### Revise, do not delete

```
> revise(entity_name="api/cache: read-through",
         observations=["read-through cache in front of the read model"],
         superseded_by="moved to write-through after the read model split",
         superseding_entity="api/cache: write-through")
```

The old observation is marked invalidated, dated, and linked to what replaced
it. Delete tools exist but sit outside the default agent-facing profiles: an
archive that forgets its own reversals cannot answer the question it was kept
for.

### Decisions record what was rejected

`record_decision` stores `decided`, `because`, and `rejected` as separately
queryable observations. The rejected alternatives are the part worth keeping —
what was chosen stays readable in the code forever, what was considered and
dropped exists nowhere else. That is the question that gets asked six months
later.

---

## MCP tools

Tool definitions sit in the context window from the first token of a session,
so the surface is a standing cost rather than a per-call one. Profiles load
only what a given step needs.

```bash
RAWTHINK_TOOL_PROFILE=recall   #  4 tools,  ~900 tokens — read-only
RAWTHINK_TOOL_PROFILE=record   #  5 tools, ~1750 tokens — the write path
RAWTHINK_TOOL_PROFILE=full     # 17 tools, ~4200 tokens — everything (default)
```

A tool outside the active profile stays an ordinary function — reachable from
the CLI and from tests. It simply is not in front of an agent that will not
call it.

### Search

| tool | what it does |
|---|---|
| `search_thoughts` | Hybrid search. `mode="overview"` gives one line per session |
| `get_session` | Full content of a session by ID |
| `store_thought` | Save a quick note as a qnote |
| `reindex` | Re-index the vault into Qdrant |

### Graph — reading

| tool | what it does |
|---|---|
| `search_nodes` | Bounded. Filters by `domain` and `entity_type`; reports `total_matched` and `truncated` |
| `open_nodes` | Specific entities with their relations |
| `read_graph` | Whole graph, paginated, with a summary mode |

### Graph — writing

| tool | what it does |
|---|---|
| `record` | Entities, relations and observations in one validated, atomic call |
| `record_decision` | A decision with its rejected alternatives |
| `revise` | Mark observations superseded, link what replaced them |
| `create_entities` · `create_relations` · `add_observations` | Lower-level equivalents |
| `invalidate_observations` | Belief revision without the relation link |
| `delete_entities` · `delete_observations` · `delete_relations` | `full` profile only |

`record()` validates the whole batch before writing any of it. A half-valid
batch writes nothing — a graph left in a state nobody asked for is worse than a
rejected write. Relations may only point at entities that already exist or are
created in the same call.

Every tool carries MCP annotations (`readOnlyHint`, `destructiveHint`,
`idempotentHint`), so a host can tell deletion apart from search.

---

## Architecture

```
Claude Code
    │  MCP (stdio)
    ▼
rawthink-mcp
    ├── search  ──►  Qdrant        dense (BGE-M3) + sparse (BM25), RRF fusion
    ├── graph   ──►  memory.jsonl  entities, relations, temporal observations
    └── export  ──►  vault/        sessions, qnotes, handoffs as markdown
                         │
                    Ollama (bge-m3)
```

Everything runs locally. The vault is plain markdown with YAML frontmatter —
open it in Obsidian to browse visually, no plugins needed.

### Why JSONL for the graph

Human-readable, git-diffable, no dependency. You can open it, read it, and see
a meaningful diff when it changes — which matters for something meant to hold
your reasoning.

The tradeoff is load time: the whole file is parsed per read. Fine at a few
hundred entities, slower as it grows. Past tens of thousands, SQLite is the
obvious next step.

---

## Session lifecycle

```
session start          handoff loads automatically (SessionStart hook)
      ↓
  think together
      ↓
   /rtclose            export → extract entities → write handoff → update MEMORY.md
```

`/rtclose` exports the conversation to clean markdown, extracts entities and
relations through `record()`, writes a project-scoped handoff, and updates
`MEMORY.md`.

The lifecycle commands currently require Claude Code. The search and graph
tools work with any MCP client.

---

## Configuration

| Setting | Env var | Default |
|---|---|---|
| Vault path | `RAWTHINK_VAULT` | `../vault` |
| Knowledge graph file | `MEMORY_FILE_PATH` | `<vault>/memory.jsonl` |
| Qdrant URL | `QDRANT_URL` | `http://localhost:6333` |
| Qdrant embedded path | `QDRANT_PATH` | — (set it to skip Docker) |
| Ollama URL | `OLLAMA_URL` | `http://localhost:11434` |
| Embedding model | `OLLAMA_MODEL` | `bge-m3` |
| Tool profile | `RAWTHINK_TOOL_PROFILE` | `full` |
| Turkish normalization | `RAWTHINK_TURKISH_NORMALIZATION` | `false` |
| Evaluation set | `RAWTHINK_EVAL_GT` | `tests/ground_truth.example.json` |

Vocabularies — `ENTITY_TYPES`, `DOMAINS`, `RELATION_TYPES`, `RELATION_ALIASES` —
live in `rawthink_mcp/config.py`. Adding a domain is a one-line change.

---

## Customization

**`CLAUDE.md`** — the thinking companion's role, tone and modes.

**`THINKING_DIRECTIVES.md`** — discipline for the partnership. Every rule was
written after failing at it. Add your own; the only bad version of that file is
one followed without understanding why each rule exists.

Both are copied into your vault by `rawthink-install`. If you edit the repo
copies, run `python scripts/check_templates.py` — the installer embeds them, and
two copies of one document drift silently.

---

## Known limitations

Stated plainly, because a README that lists only strengths is not much use.

**Concurrent writes can lose updates.** Graph mutations take no file lock. One
session at a time against a vault is safe; two are not. An earlier version of
this document claimed multi-terminal safety — the code did not support that
claim, and it has been removed rather than quietly left in.

**Ollama being unavailable degrades to sparse-only.** The embedding cache helps
repeated queries; it is not a fallback. Retrieval quality drops noticeably.

**BM25 term IDs are corpus-dependent.** Sparse vectors go stale after a reindex,
and RRF hides it because the dense side still works. Fixing it needs a full
re-encode — that is what 2.0.0 is for.

**Load time grows with the graph.** The whole JSONL is parsed on every read.

---

## Roadmap

**2.0.0** — BM25 term-ID stability (breaking; requires a full reindex),
per-vault BM25 state, atomic graph writes with a file lock, `rawthink-doctor`
for install diagnostics, a unit test suite and CI.

**Later** — graph visualisation, MCP-native session lifecycle so the close
command is not Claude Code specific, support for more MCP clients.

---

## Contributing

Issues and pull requests welcome.

If you change the schema, change `config.py`, the migration in `migrate.py`, and
the session-close instructions together. They are three views of one contract,
and they drift apart quietly when they are not edited as a set.

## License

MIT
