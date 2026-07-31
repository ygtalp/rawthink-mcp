# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [2.0.0] — 2026-07-31

> **Breaking twice over.** The graph schema changed in 1.5.0 and the sparse
> index changes here. Both need action:
>
> ```bash
> python -m rawthink_mcp.migrate --guess-domains --heal-dangling --apply   # if coming from 0.x
> rawthink-doctor                                                          # see what is still pending
> ```
> then, from a session: `reindex(full=True)`
>
> A plain reindex skips unchanged chunks and leaves the old sparse encoding in
> place — which is how a corrupt index survived the documented remedy.

### Why this release exists

BM25 term IDs were positions in a sorted vocabulary. One new token sorting
early shifted every ID after it, so vectors written before a reindex stopped
matching queries encoded after it. Nothing failed: RRF kept working off the
dense side, so search felt fine.

Measured on a real 1,841-point vault before and after the fix:

| | before | after |
|---|---|---|
| sparse MRR | 0.1596 | **0.6259** |
| sparse nDCG@10 | 0.1851 | **0.6320** |
| sparse hit@10 | 5/19 | **14/19** |
| dense MRR | 0.6842 | 0.6842 (unchanged — the isolation check) |

Dense staying identical is what makes the sparse delta attributable.

The same measurement surfaced a second problem the plan had not predicted: 40
of 69 sessions in that vault had never been indexed. The files were on disk,
the search could never find them, and nothing said so. After indexing the
sessions the queries needed, dense MRR went 0.6842 → 0.9474 and sparse 0.6259 →
0.8395. A stale index was costing more than the encoding bug. `rawthink-doctor`
now checks for exactly this.

### Added

- **`rawthink-doctor`** — nine checks with a fix line for each failure: vault,
  graph schema, BM25 state format, Qdrant, **index coverage**, vector
  dimension, Ollama, MCP registration.
- **Graceful shutdown** — SIGINT/SIGTERM handlers and a FastMCP lifespan that
  release the Qdrant directory lock and the graph file lock. A process killed
  without releasing them left the next start failing on a lock error that reads
  like corruption.
- **Concurrent writes are now safe.** Graph mutations take a file lock and a
  thread lock. Measured before the fix, with three processes writing: one
  writer's twelve updates all vanished, silently.
- **A test suite** — 101 tests, no Docker and no Ollama required. The
  dependencies sit at the edges: seven of nine modules import neither, and the
  indexer now takes an injectable embedder.
- **CI** — Python 3.10–3.13, ruff, pytest with coverage, a template-drift
  check, and a build job that fails if `tests/` reappears in the sdist.
- `index_vault(force=True)`, so a full reindex actually re-encodes.

### Changed

- **BM25 term IDs are a hash of the token**, not a position. `format_version`
  is 2; a state file from the old scheme is rejected with an error rather than
  loaded, and `initialize()` deletes it with a warning.
- **BM25 state lives with the vault**, not in the package directory. Two vaults
  on one machine used to share a single IDF table, and a read-only install
  could not write it at all.
- **Diagnostics go to stderr.** `index_vault` printed to stdout, which is the
  JSON-RPC channel — the messages either vanished or corrupted the protocol,
  and either way the record of which files were skipped never arrived.
- **`open_nodes` is a pure read.** It refreshed activation on the read path, so
  reading a git-tracked file produced a diff.
- **Graph search matches prefixes, not substrings.** `art` matched `smart`, and
  every query token scanned the whole vocabulary to find out.
- **`get_session_chunks` pages.** A flat `limit=1000` truncated long sessions
  with no indication the result was partial.
- **Embedding errors include timeouts.** Only `ConnectError` was caught, so a
  timeout escaped as a raw exception instead of reaching the cache.
- **`delete_entities` reports only what existed.** It used to claim it had
  deleted names that were never there.
- Payload indexes are created outside the create-collection branch, so an
  existing collection gains newly added fields.
- Evaluation fixtures load from `RAWTHINK_EVAL_GT`; the synthetic generator
  emits canonical entity types.

### Fixed

- A `zip` in `_embed_batch` silently truncated when the model returned fewer
  vectors than texts, leaving chunks unindexed with no error anywhere.
- `_write_all` used a fixed temp filename, so two writers clobbered each
  other's temp file. Now `mkstemp` + `fsync` + `os.replace`.
- The read cache was written but never read; every call re-parsed the file and
  rebuilt the inverted index.
- `items or self._read_all()` re-read on an empty list, because `[]` is falsy.

### Known limitations

- Retrieval quality is measured only against one private vault and a small
  synthetic one. `tests/search_quality.py` runs anywhere; the numbers it
  produces on the synthetic set are not a quality claim.
- `install.py` and `session_export.py` have no test coverage.
- Graceful shutdown is POSIX-only. Windows terminates rather than signalling,
  so a hard stop there can leave the Qdrant lock behind. Found by running the
  suite on Windows, where the SIGTERM test failed for a platform reason rather
  than a defect.
- The FastMCP lifespan hook is registered and its signature verified against
  the installed version, but it has not been observed firing. The signal
  handlers cover the same ground and are tested.

## [1.5.0] — 2026-07-31

> **Breaking. Existing vaults must be migrated before this version can write
> to them.** Run `python -m rawthink_mcp.migrate` — it reports what it would
> change and writes nothing until you pass `--apply`.
>
> ```bash
> python -m rawthink_mcp.migrate --path vault/memory.jsonl --guess-domains --heal-dangling
> python -m rawthink_mcp.migrate --path vault/memory.jsonl --guess-domains --heal-dangling --apply
> ```
>
> A timestamped backup is taken before anything is written.

### Why this release exists

A real vault reached **46 entity types and 66 relation types** across 173
entities. Two causes, both fixed here.

`entityType` was answering two questions at once — what role does this node
play, and what subject is it about — so the list grew by one entry per topic:
`saglik-bulgusu`, `teknik-karar`, `bug-pattern`. Splitting subject into its own
`domain` field collapses the type list to ten and keeps it there.

Non-canonical relation types were accepted with a warning. Nothing acted on the
warning, so 56 one-off types accumulated unnoticed — the graph was still
writable, just no longer queryable. Unknown types are now rejected.

### Added

- **`record()`** — a single validated write path for entities, relations and
  observations. All-or-nothing: a batch that is half-valid writes nothing.
  Both the session-close command and the phase pipeline go through it, so a
  rule that holds here holds everywhere.
- **`record_decision()`** — records what was decided, what was **rejected**,
  and why. The rejected alternatives are the part worth storing: what was
  chosen stays readable in the code, what was dropped exists nowhere else.
- **`revise()`** — marks observations as no longer held and links what replaced
  them. Not a delete: an archive that forgets what you used to think cannot
  answer why you changed your mind.
- **`domain` field** — subject area, separate from role. `software`, `music`,
  `history`, `philosophy`, `health`, `writing`, `neuro`, `finance`, `personal`,
  `galaxy`.
- **`visibility` field** — `private` (default) or `shareable`, per entity.
- **Observation `kind`** — `note`, `decided`, `rejected`, `because`, `touches`.
  Makes the parts of a decision queryable instead of buried in prose.
- **Tool profiles** — `RAWTHINK_TOOL_PROFILE=recall|record|full`. Tool
  definitions occupy the context window from the first token of a session, so
  the surface is a standing cost. `recall` is 4 tools (~900 tokens), `record`
  is 5 (~1,750), `full` is 17 (~4,200).
- **MCP tool annotations** — `readOnlyHint`, `destructiveHint`,
  `idempotentHint` on every tool, so a host can tell deletion apart from search.
- **`migrate.py`** — one-off schema migration with dry-run, backup, optional
  domain inference and dangling-edge repair.
- **`scripts/check_templates.py`** — fails if an embedded install template has
  drifted from its repo file.
- **Dangling-edge protection** — a relation may only point at an entity that
  exists or is created in the same call.
- Three new thinking directives, earned from real failures: silent failure as
  a distinct severity class, proxy measurement, and review versus execution.

### Changed

- **`entityType` is required and closed** to ten roles: `decision`, `concept`,
  `finding`, `rule`, `open-question`, `artifact`, `insight`, `task`, `event`,
  `thing`. It no longer defaults to `concept`.
- **Relation types are folded or rejected.** Close synonyms (`connected_to`,
  `aspect_of`, `demonstrates`, `extends`, and 16 more) normalise to the
  canonical form; anything unrecognised raises. Canonical vocabulary gained
  `investigates`, `informs` and `uses`.
- **`search_nodes` is bounded** — `limit`, `max_relations`, and optional
  `domain` / `entity_type` filters. Returns `total_matched` and a `truncated`
  flag so a cut result is visible rather than silent.
- **`get_related` merged into `search_thoughts`** as `mode="overview"`. They
  called the same search with different formatting, and two tools with
  near-identical descriptions is how a model picks the wrong one.
- **`serverInfo.version` reports the package version.** It previously reported
  FastMCP's — not blank, but plausibly wrong, which is harder to notice.
- **`epistemic` defaults to `unknown`, never `assertion`.** An unstated claim
  must not be promoted to a stated one, in migration or at write time.
- Session-close instructions (`/rtclose`, and the copy embedded in the
  installer) rewritten for the controlled vocabulary.
- Synthetic test data generator emits canonical entity types.

### Fixed

- **The published sdist contained `tests/`**, whose evaluation fixtures
  hardcoded personal entity names and session dates. `pyproject.toml` now has
  an explicit sdist allowlist, and the evaluation set loads from
  `RAWTHINK_EVAL_GT`, defaulting to a synthetic set that runs anywhere.
- **The installer's embedded `THINKING_DIRECTIVES.md` had fallen 65 lines
  behind** the repo file, so installed users received half the document.
- **Line endings.** A `.gitattributes` was missing, so every tracked file
  showed as modified on a Windows checkout and real changes hid in the noise.

### Removed

- `get_related` as a separate MCP tool — see `search_thoughts(mode="overview")`.
- `reindex`, `read_graph` and the three `delete_*` tools are absent from the
  `recall` and `record` profiles. They still exist and are still reachable
  under `full`; they are simply not in front of an agent that will not use them.

### Known limitations

- Graph writes take no file lock. Concurrent sessions writing the same vault
  can lose an update. Earlier documentation claimed multi-terminal safety;
  that claim was not supported by the code and has been removed.
- Ollama being unavailable degrades to sparse-only retrieval. The embedding
  cache helps repeated queries; it is not a fallback.
- BM25 term IDs are corpus-dependent, so sparse vectors go stale after a
  reindex. Fixing it requires a full re-encode and is planned for 2.0.0.

## [0.1.4] — 2026-04-20

- Consistent project directory for pip installs; `rawthink-install` creates a
  single directory with the vault inside.
- `config.py` MEMORY_FILE fix, `rtclose` entry-point fix, upgrade-safe hook.

## [0.1.0] — 2026-04-11

- Initial public release: hybrid search, knowledge graph, session lifecycle.
