"""FastMCP server exposing RAWThink vault search + knowledge graph tools.

Search tools:
  - search_thoughts: Hybrid semantic + keyword search
  - get_session: Retrieve full session content
  - get_related: Quick related thoughts lookup
  - store_thought: Store a new thought/qnote
  - reindex: Re-index vault content

Knowledge graph tools:
  - search_nodes: Search entities by name/type/observations
  - create_entities: Create or merge entities (with epistemic typing + activation)
  - create_relations: Create relations (controlled vocabulary)
  - add_observations: Add observations to entities (temporal metadata)
  - invalidate_observations: Mark observations as invalidated (belief revision)
  - delete_entities: Delete entities and their relations
  - delete_observations: Remove specific observations
  - delete_relations: Remove specific relations
  - read_graph: Return full graph (with activation decay + epistemic info)
  - open_nodes: Return specific entities with relations
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny

from .indexer import Indexer, EmbeddingError
from .graph import KnowledgeGraph, SchemaError
from . import config

def _package_version() -> str:
    """Report OUR version, not the framework's.

    FastMCP falls back to its own version when none is given, so a client
    asking what it is talking to got FastMCP's number and no way to tell.
    A plausible-looking wrong version is worse than an empty one.
    """
    try:
        from importlib.metadata import version
        return version("rawthink-mcp")
    except Exception:
        return "0.0.0+unknown"


# ---------------------------------------------------------------------------
# Tool profiles
#
# Every tool definition sits in the context window from the first token of a
# session, so the surface is a standing cost, not a per-call one. Profiles let
# a caller load only what that step actually needs: a phase pipeline writing a
# decision has no business holding delete tools.
#
#   recall — read-only lookup
#   record — the write path, plus enough lookup to avoid duplicates
#   full   — everything, including destructive and maintenance tools
#
# Set RAWTHINK_TOOL_PROFILE. Default is `full`, so existing setups are
# unaffected until they opt in.
# ---------------------------------------------------------------------------

_PROFILES: dict[str, set[str] | None] = {
    "recall": {"search_thoughts", "search_nodes", "get_session", "open_nodes"},
    "record": {"search_nodes", "record", "record_decision", "revise", "store_thought"},
    "full": None,  # None = register everything
}

_PROFILE = os.environ.get("RAWTHINK_TOOL_PROFILE", "full").strip().lower()
if _PROFILE not in _PROFILES:
    _PROFILE = "full"

_ALLOWED = _PROFILES[_PROFILE]

mcp = FastMCP(
    name="rawthink",
    version=_package_version(),
    instructions=(
        "Semantic search across your RAWThink vault — sessions, "
        "qnotes, and personal insights. Use search_thoughts for natural "
        "language queries like 'what did I think about free will?'\n\n"
        "Knowledge graph tools use a controlled vocabulary: entityType is the "
        "role a node plays (decision, concept, finding, rule, open-question, "
        "artifact, insight, task, event, thing) and `domain` is the subject "
        "area. Write through record() or record_decision(); they validate "
        "before writing and reject unknown types rather than warning.\n\n"
        "To revise a belief use revise(), never delete — an archive that "
        "forgets what you used to think cannot answer why you changed your mind."
    ),
)


def _tool(**kw):
    """Register a tool only when the active profile includes it.

    A tool left unregistered stays an ordinary function: still importable,
    still callable from the CLI and from tests. It simply does not consume
    context in sessions that will never call it.
    """
    def deco(fn):
        if _ALLOWED is not None and fn.__name__ not in _ALLOWED:
            return fn
        return mcp.tool(**kw)(fn)
    return deco

# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------
_indexer: Indexer | None = None
_graph: KnowledgeGraph | None = None


def _get_indexer() -> Indexer:
    global _indexer
    if _indexer is None:
        idx = Indexer()
        idx.initialize()
        _indexer = idx  # Only set after successful init
    return _indexer


def _get_graph() -> KnowledgeGraph:
    global _graph
    if _graph is None:
        _graph = KnowledgeGraph()
    return _graph


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@_tool(annotations={'readOnlyHint': True, 'idempotentHint': True, 'openWorldHint': False})
def search_thoughts(
    query: str,
    source_type: Optional[str] = None,
    tags: Optional[list[str]] = None,
    limit: int = 10,
    mode: str = "full",
) -> str:
    """Search vault content with hybrid semantic + keyword search.

    Args:
        query: Natural language query (e.g. "what did I think about consciousness?").
        source_type: Filter by "session" or "qnote".
        tags: Filter by tags (e.g. ["philosophy", "consciousness"]).
        limit: Max results (default 10).
        mode: "full" returns matching passages; "overview" returns one line per
            session (id, title, score) for orientation before drilling in.
    """
    idx = _get_indexer()

    conditions = []
    if source_type:
        conditions.append(
            FieldCondition(key="source_type", match=MatchValue(value=source_type))
        )
    if tags:
        conditions.append(
            FieldCondition(key="tags", match=MatchAny(any=tags))
        )

    filters = Filter(must=conditions) if conditions else None

    try:
        results = idx.search(query=query, limit=limit, filters=filters)
    except EmbeddingError as exc:
        return f"**Embedding error:** {exc}"

    if not results:
        return "No results found."

    if mode == "overview":
        # One line per session, deduplicated. Was a separate get_related tool;
        # it called the same search with a terser format, and two tools with
        # near-identical descriptions is how a model picks the wrong one.
        parts: list[str] = []
        seen: set[str] = set()
        for hit in results:
            session = hit.get("session_id", "")
            if session in seen:
                continue
            seen.add(session)
            parts.append(
                f"- **[{session}]** {hit.get('title','')} — "
                f"{hit.get('section_heading','')} "
                f"(score: {hit.get('score',0):.4f}, {hit.get('date','')})"
            )
        return "\n".join(parts)

    parts: list[str] = []
    for i, hit in enumerate(results, 1):
        session = hit.get("session_id", "")
        title = hit.get("title", "")
        section = hit.get("section_heading", "")
        score = hit.get("score", 0)
        text = hit.get("chunk_text", "")
        date = hit.get("date", "")

        # Truncate text for readability
        preview = text[:500] + "..." if len(text) > 500 else text

        parts.append(
            f"### {i}. [{session}] {title}\n"
            f"**Date:** {date} | **Section:** {section} | **Score:** {score:.4f}\n\n"
            f"{preview}"
        )

    return "\n\n---\n\n".join(parts)


@_tool(annotations={'readOnlyHint': True, 'idempotentHint': True, 'openWorldHint': False})
def get_session(session_id: str) -> str:
    """Get full content of a session by reading the markdown file directly.

    Args:
        session_id: Session identifier (e.g. "2026-03-16_001").
    """
    vault = Path(config.VAULT_PATH).resolve()

    # Try to find session file
    import glob as g
    pattern = str(vault / "sessions" / f"*{session_id}*.md")
    files = g.glob(pattern)

    if files:
        content = Path(files[0]).read_text(encoding="utf-8")
        return content

    # Fallback: get from Qdrant chunks
    idx = _get_indexer()
    chunks = idx.get_session_chunks(session_id)

    if not chunks:
        return f"Session '{session_id}' not found."

    parts = []
    for chunk in chunks:
        parts.append(chunk.get("chunk_text", ""))

    return "\n\n---\n\n".join(parts)


@_tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False})
def store_thought(
    text: str,
    session_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> str:
    """Store a new thought — embed and save to vault as a qnote.

    Args:
        text: The thought text to store.
        session_id: Session reference linking this qnote to its source conversation.
            Recommended — pass the current or most recent session ID (e.g. from
            handoff) so qnotes can be traced back to their session context.
        tags: Optional tags for the note.
    """
    idx = _get_indexer()
    vault = Path(config.VAULT_PATH).resolve()
    qnotes_dir = vault / "qnotes"
    qnotes_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")
    filename = f"{date_str}_{time_str}.md"

    # Build frontmatter
    tags_list = tags or []
    tags_yaml = ", ".join(f'"{t}"' for t in tags_list)
    session_ref = session_id or ""

    content = f"""---
date: {date_str}
tags: [{tags_yaml}]
session_ref: "{session_ref}"
---

> {text}
"""

    # Write file
    filepath = qnotes_dir / filename
    filepath.write_text(content, encoding="utf-8")

    # Index into Qdrant
    from .chunker import ThoughtChunk
    import hashlib

    chunk = ThoughtChunk(
        session_id=f"qnote_{date_str}_{time_str}",
        title="Quick Note",
        date=date_str,
        tags=tags_list,
        section_heading="",
        chunk_text=text,
        chunk_index=0,
        line_start=1,
        line_end=1,
        content_hash=hashlib.sha256(text.encode()).hexdigest()[:32],
        source_type="qnote",
    )

    try:
        count = idx.index_chunk(chunk)
        return f"Stored qnote: {filename} ({count} chunks indexed)"
    except EmbeddingError as exc:
        return f"Saved file but embedding failed: {exc}"


@_tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True})
def reindex(session_id: Optional[str] = None, full: bool = False) -> str:
    """Re-index vault content into the search database.

    Args:
        session_id: If provided, reindex only this session.
        full: Force full vault re-index.
    """
    idx = _get_indexer()

    try:
        if session_id:
            idx.delete_session(session_id)
            count = idx.index_session(session_id)
            return f"Reindexed session '{session_id}': {count} chunks."
        elif full:
            count = idx.index_vault()
            return f"Full vault reindex complete: {count} chunks."
        else:
            count = idx.index_vault()
            return f"Vault indexed: {count} chunks."
    except EmbeddingError as exc:
        return f"**Embedding error:** {exc}"


# ---------------------------------------------------------------------------
# Knowledge Graph Tools
# ---------------------------------------------------------------------------


@_tool(annotations={'readOnlyHint': True, 'idempotentHint': True, 'openWorldHint': False})
def search_nodes(
    query: str,
    limit: int = 10,
    max_relations: int = 50,
    domain: Optional[str] = None,
    entity_type: Optional[str] = None,
) -> str:
    """Search the knowledge graph. Bounded: returns `total_matched` alongside
    a capped page, so a truncated result is visible rather than silent.

    Args:
        query: Matched against entity names, types and observations.
        limit: Max entities returned (default 10).
        max_relations: Max relations returned (default 50).
        domain: Narrow to one subject area (software, music, history, ...).
        entity_type: Narrow to one role (decision, concept, finding, rule, ...).
    """
    kg = _get_graph()
    try:
        result = kg.search_nodes(query, limit=limit, max_relations=max_relations,
                                 domain=domain, entity_type=entity_type)
    except SchemaError as exc:
        return f"**Schema error:** {exc}"
    return json.dumps(result, ensure_ascii=False, indent=2)


@_tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True})
def create_entities(entities: list[dict]) -> str:
    """Create multiple new entities in the knowledge graph.

    If an entity with the same name exists, new observations are merged.

    Each entity can have:
    - name (required): Entity name
    - entityType: Type (default "concept")
    - observations: List of observation strings
    - epistemic: "assertion" | "hypothesis" | "speculation" (optional)

    Observations are stored with temporal metadata (created date, status).
    Entities get activation tracking (activation=1.0, last_accessed=today).

    Args:
        entities: List of dicts with keys: name, entityType, observations (list of strings), epistemic (optional).
    """
    kg = _get_graph()
    created = kg.create_entities(entities)
    return json.dumps(created, ensure_ascii=False, indent=2)


@_tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True})
def create_relations(relations: list[dict]) -> str:
    """Create multiple new relations between entities. Relations should be in active voice.

    Canonical relation types: supports, contradicts, evolved_into, depends_on,
    exemplifies, part_of, caused_by, enables, supersedes, related_to.

    Non-standard types are accepted but return a warning.

    Args:
        relations: List of dicts with keys: from, to, relationType.
    """
    kg = _get_graph()
    created = kg.create_relations(relations)
    return json.dumps(created, ensure_ascii=False, indent=2)


@_tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True})
def add_observations(observations: list[dict]) -> str:
    """Add new observations to existing entities in the knowledge graph.

    Args:
        observations: List of dicts with keys: entityName, contents (list of strings).
    """
    kg = _get_graph()
    results = kg.add_observations(observations)
    return json.dumps(results, ensure_ascii=False, indent=2)


@_tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True})
def record(
    entities: Optional[list[dict]] = None,
    relations: Optional[list[dict]] = None,
    observations: Optional[list[dict]] = None,
) -> str:
    """Write entities, relations and observations in one validated, atomic call.

    This is the single write path. Everything is validated before anything is
    written, so a batch that is half-valid writes nothing.

    Entity fields: name, entityType (required for new), domain (required for
    new), epistemic (assertion|hypothesis|speculation|unknown, default
    unknown), visibility (private|shareable, default private), observations.
    An observation may be a string, or {"text": ..., "kind": ...} where kind is
    note|decided|rejected|because|touches.

    Relations use the canonical vocabulary; close synonyms are folded, unknown
    types are rejected. A relation may only point at an entity that exists or
    is being created in this same call.

    Args:
        entities: Entities to create or merge into.
        relations: Relations to create.
        observations: [{"entityName": ..., "contents": [...], "kind": ...}]
    """
    kg = _get_graph()
    try:
        return json.dumps(kg.record(entities, relations, observations),
                          ensure_ascii=False, indent=2)
    except SchemaError as exc:
        return f"**Schema error:** {exc}"


@_tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True})
def record_decision(
    name: str,
    domain: str,
    decided: str,
    because: str,
    rejected: Optional[list[str]] = None,
    touches: Optional[list[str]] = None,
    epistemic: str = "assertion",
    visibility: Optional[str] = None,
    supersedes: Optional[str] = None,
) -> str:
    """Record a decision: what was chosen, what was rejected, and why.

    `rejected` is the field that makes this worth recording. What was chosen
    stays readable in the code forever; what was considered and dropped exists
    nowhere else, and it is the question that gets asked months later.

    Args:
        name: Stable identifier, e.g. "banking/phase-7: reference ID strategy".
        domain: Subject area (software, music, history, ...).
        decided: What was chosen.
        because: The constraint or reasoning that forced it.
        rejected: Alternatives considered and dropped, each with its reason.
        touches: Files or symbols this decision governs.
        epistemic: assertion | hypothesis | speculation | unknown.
        visibility: private (default) | shareable.
        supersedes: Name of a decision this one replaces.
    """
    kg = _get_graph()
    try:
        return json.dumps(
            kg.record_decision(name=name, domain=domain, decided=decided,
                               because=because, rejected=rejected, touches=touches,
                               epistemic=epistemic, visibility=visibility,
                               supersedes=supersedes),
            ensure_ascii=False, indent=2)
    except SchemaError as exc:
        return f"**Schema error:** {exc}"


@_tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True})
def revise(
    entity_name: str,
    observations: list[str],
    superseded_by: Optional[str] = None,
    superseding_entity: Optional[str] = None,
) -> str:
    """Mark observations as no longer held, and link what replaced them.

    Deliberately not a delete. Keeping the superseded belief, dated and linked,
    is what lets the archive answer "why did we change our mind" later.

    If you pass `superseding_entity`, record that entity first — a relation
    cannot point at a name the graph does not know.

    Args:
        entity_name: Entity whose observations are being revised.
        observations: Exact observation texts to invalidate.
        superseded_by: Short description of what replaced them.
        superseding_entity: Name of the entity that supersedes this one.
    """
    kg = _get_graph()
    try:
        return json.dumps(
            kg.revise(entity_name, observations, superseded_by, superseding_entity),
            ensure_ascii=False, indent=2)
    except SchemaError as exc:
        return f"**Schema error:** {exc}"


@_tool(annotations={'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True})
def delete_entities(entityNames: list[str]) -> str:
    """Delete multiple entities and their associated relations from the knowledge graph.

    Args:
        entityNames: List of entity names to delete.
    """
    kg = _get_graph()
    deleted = kg.delete_entities(entityNames)
    return json.dumps(deleted, ensure_ascii=False, indent=2)


@_tool(annotations={'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True})
def delete_observations(deletions: list[dict]) -> str:
    """Delete specific observations from entities in the knowledge graph.

    Args:
        deletions: List of dicts with keys: entityName, observations (list of strings to remove).
    """
    kg = _get_graph()
    results = kg.delete_observations(deletions)
    return json.dumps(results, ensure_ascii=False, indent=2)


@_tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True})
def invalidate_observations(
    entity_name: str,
    observations: list[str],
    superseded_by: Optional[str] = None,
) -> str:
    """Mark observations as invalidated (belief revision tracking).

    Sets status='invalidated' and records invalidated_at date.
    Optionally records what superseded the old belief.

    Args:
        entity_name: Name of the entity whose observations to invalidate.
        observations: List of observation text strings to invalidate.
        superseded_by: Optional description of what replaced these beliefs.
    """
    kg = _get_graph()
    result = kg.invalidate_observations(entity_name, observations, superseded_by)
    return json.dumps(result, ensure_ascii=False, indent=2)


@_tool(annotations={'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True})
def delete_relations(relations: list[dict]) -> str:
    """Delete multiple relations from the knowledge graph.

    Args:
        relations: List of dicts with keys: from, to, relationType.
    """
    kg = _get_graph()
    results = kg.delete_relations(relations)
    return json.dumps(results, ensure_ascii=False, indent=2)


@_tool(annotations={'readOnlyHint': True, 'idempotentHint': True, 'openWorldHint': False})
def read_graph(
    summary: bool = False,
    entity_type: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
) -> str:
    """Read the entire knowledge graph — all entities and relations.

    By default returns full entities with pagination (offset/limit).
    Use summary=True for a compact overview (names + types + observation counts).

    Args:
        summary: If True, return compact overview. If False (default), return full entities with pagination.
        entity_type: Filter entities by type (e.g. "concept", "decision", "insight").
        offset: Skip first N entities (for pagination). Default 0.
        limit: Max entities to return (for pagination). Default 20.
    """
    kg = _get_graph()
    data = kg.read_graph()
    entities = data["entities"]
    relations = data["relations"]

    # Optional type filter
    if entity_type:
        entities = [e for e in entities if e.get("entityType", "").lower() == entity_type.lower()]

    total_entities = len(entities)
    total_relations = len(relations)

    if summary:
        from .graph import KnowledgeGraph as _KG
        # Compact overview: name, type, observation count, epistemic, activation
        lines = [f"**Knowledge Graph**: {total_entities} entities, {total_relations} relations\n"]

        # Group by type
        by_type: dict[str, list[dict]] = {}
        for e in entities:
            t = e.get("entityType", "unknown")
            by_type.setdefault(t, []).append(e)

        for etype, ents in sorted(by_type.items()):
            lines.append(f"\n### {etype} ({len(ents)})")
            for e in sorted(ents, key=lambda x: x["name"]):
                obs_count = len(e.get("observations", []))
                parts = [f"**{e['name']}** ({obs_count} obs)"]
                ep = e.get("epistemic")
                if ep:
                    parts.append(f"[{ep}]")
                act = _KG._decay_activation(e)
                if act < 0.99:
                    parts.append(f"act={act:.2f}")
                lines.append(f"- {' '.join(parts)}")

        return "\n".join(lines)
    else:
        # Full details with pagination
        page = entities[offset : offset + limit]
        # Only include relations connected to returned entities
        page_names = {e["name"] for e in page}
        page_relations = [
            r for r in relations
            if r.get("from") in page_names or r.get("to") in page_names
        ]

        result = {
            "total_entities": total_entities,
            "total_relations": total_relations,
            "offset": offset,
            "limit": limit,
            "entities": page,
            "relations": page_relations,
        }
        return json.dumps(result, ensure_ascii=False, indent=2)


@_tool(annotations={'readOnlyHint': True, 'idempotentHint': True, 'openWorldHint': False})
def open_nodes(names: list[str]) -> str:
    """Open specific nodes in the knowledge graph by their names.

    Returns the requested entities and all relations connected to them.

    Args:
        names: List of entity names to retrieve.
    """
    kg = _get_graph()
    result = kg.open_nodes(names)
    return json.dumps(result, ensure_ascii=False, indent=2)


def main():
    """Entry point for the MCP server."""
    # Eager initialization — warm up Qdrant + graph at server start
    # so first tool call doesn't pay initialization cost
    try:
        _get_graph()
    except Exception:
        global _graph
        _graph = None  # Reset for lazy retry
    try:
        _get_indexer()
    except Exception:
        global _indexer
        _indexer = None  # Reset for lazy retry (Qdrant lock or Ollama down)
    mcp.run()


if __name__ == "__main__":
    main()
