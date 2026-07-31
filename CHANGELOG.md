# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

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
