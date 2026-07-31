"""JSONL-backed knowledge graph with Turkish character normalization.

Drop-in replacement for @modelcontextprotocol/server-memory graph operations.
Reads/writes the same memory.jsonl format.

Tier 1: Atomic writes, temporal metadata on observations, epistemic typing,
        controlled relation vocabulary.
Tier 2: Activation decay, in-memory inverted index for search.
"""
from __future__ import annotations

import bisect
import functools
import json
import math
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from filelock import FileLock

from . import config

# ---------------------------------------------------------------------------
# Turkish normalization
# ---------------------------------------------------------------------------
_TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def normalize_turkish(text: str) -> str:
    """Normalize Turkish characters and lowercase for search matching."""
    if not config.ENABLE_TURKISH_NORMALIZATION:
        return text.lower()
    return text.translate(_TR_MAP).lower()


def _today() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Observation helpers
# ---------------------------------------------------------------------------

# Observation kinds. A decision's parts stay queryable instead of being buried
# in prose: "we chose X" is readable from the code forever, "we did not choose
# Y, because Z" exists nowhere else.
OBSERVATION_KINDS = {"note", "decided", "rejected", "because", "touches"}


def _normalize_observation(obs) -> dict:
    """Normalize a legacy string observation into the temporal dict format."""
    if isinstance(obs, str):
        return {"text": obs, "status": "active"}
    return obs


def validate_observation_kind(value: str | None) -> str:
    if value is None or value == "":
        return "note"
    v = str(value).strip().lower()
    if v not in OBSERVATION_KINDS:
        raise SchemaError(
            f"Unknown observation kind '{value}'. Valid: {_fmt(OBSERVATION_KINDS)}"
        )
    return v


def _obs_text(obs) -> str:
    """Extract text from an observation (dict or legacy string)."""
    if isinstance(obs, dict):
        return obs.get("text", "")
    return str(obs)


# ---------------------------------------------------------------------------
# Schema validation
#
# These are the single point of enforcement. Both writers — the session-close
# command and the phase pipeline — go through them, so a vocabulary that holds
# here holds everywhere. A warning that callers can ignore is not enforcement:
# this vault reached 46 entity types and 66 relation types under a scheme that
# only warned.
# ---------------------------------------------------------------------------

class SchemaError(ValueError):
    """A write was rejected because it does not match the controlled schema."""


def _fmt(values) -> str:
    return ", ".join(sorted(values))


def validate_entity_type(value: str | None) -> str:
    """Entity type must come from the closed list. No default guessing."""
    if not value:
        raise SchemaError(
            f"entityType is required. Valid types: {_fmt(config.ENTITY_TYPES)}"
        )
    v = value.strip().lower()
    if v not in config.ENTITY_TYPES:
        raise SchemaError(
            f"Unknown entityType '{value}'. Valid types: {_fmt(config.ENTITY_TYPES)}. "
            f"If this is about a subject area rather than a role, it belongs in "
            f"`domain`, not `entityType`."
        )
    return v


def validate_domain(value: str | None) -> str:
    """Domain must come from the known list; extend config.DOMAINS to add one."""
    if not value:
        raise SchemaError(
            f"domain is required. Known domains: {_fmt(config.DOMAINS)}"
        )
    v = value.strip().lower()
    if v not in config.DOMAINS:
        raise SchemaError(
            f"Unknown domain '{value}'. Known domains: {_fmt(config.DOMAINS)}. "
            f"Add it to config.DOMAINS if this is a subject area you work in."
        )
    return v


def validate_epistemic(value: str | None) -> str:
    """Epistemic status. Absent means 'unknown', which is not the same as
    'assertion' — never silently upgrade an unstated claim."""
    if value is None or value == "":
        return config.DEFAULT_EPISTEMIC
    v = str(value).strip().lower()
    if v not in config.EPISTEMIC_VALUES:
        raise SchemaError(
            f"Unknown epistemic '{value}'. Valid: {_fmt(config.EPISTEMIC_VALUES)}"
        )
    return v


def validate_visibility(value: str | None) -> str:
    """Defaults to private. Sharing is opt-in, per entity."""
    if value is None or value == "":
        return config.DEFAULT_VISIBILITY
    v = str(value).strip().lower()
    if v not in config.VISIBILITY_VALUES:
        raise SchemaError(
            f"Unknown visibility '{value}'. Valid: {_fmt(config.VISIBILITY_VALUES)}"
        )
    return v


def normalize_relation_type(value: str | None) -> str:
    """Fold known synonyms into the canonical form; reject anything else.

    Returns the canonical type. Raises on an unrecognised type rather than
    accepting it with a warning — the warning is what let 56 one-off relation
    types into a 165-relation graph.
    """
    if not value:
        raise SchemaError(
            f"relationType is required. Canonical types: {_fmt(config.RELATION_TYPES)}"
        )
    v = value.strip().lower().replace(" ", "_").replace("-", "_")
    v = config.RELATION_ALIASES.get(v, v)
    if v not in config.RELATION_TYPES:
        raise SchemaError(
            f"Unknown relationType '{value}'. Canonical types: "
            f"{_fmt(config.RELATION_TYPES)}. If the connection needs a more "
            f"specific description, put it in an observation and use the "
            f"closest canonical type here."
        )
    return v


# ---------------------------------------------------------------------------
# Knowledge Graph
# ---------------------------------------------------------------------------

def _serialized(method):
    """Run a mutating method under both locks, on a freshly read graph.

    Read-modify-write without a lock is how two sessions lose each other's
    updates: the second reads before the first has written, then overwrites it.
    Applied as a decorator so every mutation gets it and none can be forgotten
    by writing a new one that looks like the others.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._rlock, self._flock:
            self._cached_items = None      # force the next read to hit disk
            self._cache_sig = None
            return method(self, *args, **kwargs)
    return wrapper


class KnowledgeGraph:
    """JSONL-backed knowledge graph with Turkish-aware search."""

    def __init__(self, path: str | None = None):
        self._path = Path(path or config.MEMORY_FILE).resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, set[str]] | None = None  # token -> entity names
        self._sorted_tokens: list[str] = []             # for prefix search
        self._cached_items: list[dict] | None = None
        self._cache_sig: tuple[int, int] | None = None  # (mtime_ns, size)
        # Two locks, deliberately. FileLock serialises across processes — several
        # Claude Code sessions can hold the same vault open. RLock serialises
        # across threads, because FastMCP may run tools in a pool and a
        # process-level lock is reentrant from inside one process, so it would
        # let two threads through.
        self._rlock = threading.RLock()
        self._flock = FileLock(str(self._path) + ".lock", timeout=30)

    # --- internal I/O ---

    def _read_all(self, force: bool = False) -> list[dict]:
        """Read the graph, reusing the cache when the file has not moved.

        The cache was written but never read, so every tool call re-parsed the
        whole file and rebuilt the inverted index. The signature is
        (mtime_ns, size): cheap, and it catches both an edit and a same-size
        rewrite by a different process.

        `force=True` skips the cache — mutations need the current file, not
        what this process last saw.
        """
        if not self._path.exists():
            return []
        if not force and self._cached_items is not None:
            try:
                st = self._path.stat()
                if self._cache_sig == (st.st_mtime_ns, st.st_size):
                    return self._cached_items
            except OSError:
                pass
        lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        items = []
        for line in lines:
            line = line.strip()
            if line:
                item = json.loads(line)
                # Normalize legacy string observations to temporal dicts
                if item.get("type") == "entity":
                    item["observations"] = [
                        _normalize_observation(o)
                        for o in item.get("observations", [])
                    ]
                items.append(item)
        self._cached_items = items
        try:
            st = self._path.stat()
            self._cache_sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._cache_sig = None
        self._build_index(items)
        return items

    def _write_all(self, items: list[dict]) -> None:
        """Atomic write.

        The old version used a fixed `.jsonl.tmp` name, so two processes writing
        at once clobbered each other's temp file and one update vanished with no
        error. mkstemp gives each writer its own file; fsync makes the content
        durable before the rename; os.replace is atomic, so a reader sees either
        the whole old file or the whole new one — never a half-written graph.
        """
        text = "\n".join(json.dumps(item, ensure_ascii=False) for item in items)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent),
                                   prefix=".memory-", suffix=".jsonl.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, str(self._path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._cached_items = None
        self._cache_sig = None
        self._index = None
        self._sorted_tokens = []

    @contextmanager
    def _transaction(self):
        """The single mutation path: lock, read fresh, yield, write.

        Every mutating method goes through this. Read-modify-write without a
        lock is how concurrent sessions lose each other's updates — the second
        writer reads before the first has written, and overwrites it.
        """
        with self._rlock, self._flock:
            items = self._read_all(force=True)
            yield items
            self._write_all(items)

    def _entities(self, items: list[dict] | None = None) -> list[dict]:
        # `items or self._read_all()` re-read on an empty list, because [] is
        # falsy — an empty graph cost a second full parse on every call.
        source = self._read_all() if items is None else items
        return [i for i in source if i.get("type") == "entity"]

    def _relations(self, items: list[dict] | None = None) -> list[dict]:
        source = self._read_all() if items is None else items
        return [i for i in source if i.get("type") == "relation"]

    # --- inverted index (Tier 2) ---

    def _build_index(self, items: list[dict]) -> None:
        """Build inverted index: normalized token -> set of entity names."""
        idx: dict[str, set[str]] = {}
        for item in items:
            if item.get("type") != "entity":
                continue
            name = item.get("name", "")
            # Split kebab-case entity names so "vitamin-d-eksikligi" becomes
            # tokens: "vitamin", "d", "eksikligi" (in addition to the full name)
            name_expanded = name.replace("-", " ")
            searchable = " ".join([
                name,
                name_expanded,
                item.get("entityType", ""),
                item.get("epistemic", ""),
                " ".join(_obs_text(o) for o in item.get("observations", [])),
            ])
            tokens = set(normalize_turkish(searchable).split())
            for tok in tokens:
                if tok:
                    idx.setdefault(tok, set()).add(name)
        self._index = idx
        self._sorted_tokens = sorted(idx)

    # --- activation decay (Tier 2) ---

    @staticmethod
    def _decay_activation(entity: dict) -> float:
        """Compute current decayed activation score."""
        base = entity.get("activation", 1.0)
        last = entity.get("last_accessed")
        if not last:
            return base
        try:
            last_date = datetime.fromisoformat(last).date()
            days = (date.today() - last_date).days
            if days <= 0:
                return base
            return base * math.exp(-config.ACTIVATION_DECAY_LAMBDA * days)
        except (ValueError, TypeError):
            return base

    def _touch_entities(self, names: set[str], items: list[dict]) -> bool:
        """Refresh activation + last_accessed for named entities. Returns True if any changed."""
        today = _today()
        changed = False
        for item in items:
            if item.get("type") == "entity" and item.get("name") in names:
                item["activation"] = 1.0
                item["last_accessed"] = today
                changed = True
        return changed

    # --- public API ---

    def search_nodes(self, query: str, limit: int = 10,
                     max_relations: int = 50,
                     domain: str | None = None,
                     entity_type: str | None = None) -> dict:
        """Search entities by name, type and observations, bounded.

        Unbounded search was the real context hazard here: a broad query on a
        large graph returned every match plus every relation touching them.
        `limit` caps what comes back; `total_matched` still reports how many
        there were, so a truncated result is visible rather than silent.
        """
        all_items = self._read_all()
        nq = normalize_turkish(query)
        tokens = nq.split()

        # Use inverted index for fast lookup with token match scoring
        # Each candidate gets a score = number of query tokens matched
        candidate_scores: dict[str, int] = {}
        if self._index and tokens:
            for tok in tokens:
                matched_names: set[str] = set()
                # Prefix, not substring. Substring made 'art' match 'smart' and
                # scanned the whole vocabulary for every query token. bisect on
                # the sorted token list finds the prefix block directly.
                lo = bisect.bisect_left(self._sorted_tokens, tok)
                for idx_tok in self._sorted_tokens[lo:]:
                    if not idx_tok.startswith(tok):
                        break
                    matched_names.update(self._index[idx_tok])
                for name in matched_names:
                    candidate_scores[name] = candidate_scores.get(name, 0) + 1
        else:
            # Fallback: linear scan
            for entity in self._entities(all_items):
                name_expanded = entity.get("name", "").replace("-", " ")
                searchable = " ".join([
                    entity.get("name", ""),
                    name_expanded,
                    entity.get("entityType", ""),
                    " ".join(_obs_text(o) for o in entity.get("observations", [])),
                ])
                ns = normalize_turkish(searchable)
                match_count = sum(1 for tok in tokens if tok in ns)
                if match_count > 0:
                    candidate_scores[entity["name"]] = match_count

        candidate_names = set(candidate_scores.keys())

        entities = [e for e in self._entities(all_items) if e["name"] in candidate_names]

        # Sort by token match count (primary) * decayed activation (secondary)
        entities.sort(
            key=lambda e: (
                candidate_scores.get(e["name"], 0),
                self._decay_activation(e),
            ),
            reverse=True,
        )

        # Optional structural filters — the point of splitting entityType from
        # domain is being able to narrow on either one.
        if entity_type:
            et = validate_entity_type(entity_type)
            entities = [e for e in entities if e.get("entityType") == et]
        if domain:
            dm = validate_domain(domain)
            entities = [e for e in entities if e.get("domain") == dm]

        total_matched = len(entities)
        page = entities[:max(0, limit)]
        page_names = {e["name"] for e in page}

        relations = [
            r for r in self._relations(all_items)
            if r.get("from") in page_names or r.get("to") in page_names
        ]
        total_relations = len(relations)
        relations = relations[:max(0, max_relations)]

        return {
            "entities": page,
            "relations": relations,
            "total_matched": total_matched,
            "total_relations": total_relations,
            "truncated": total_matched > len(page) or total_relations > len(relations),
        }

    @_serialized
    def create_entities(self, entities: list[dict]) -> list[dict]:
        """Create entities, merging observations if name already exists."""
        all_items = self._read_all()
        existing = {e["name"]: e for e in self._entities(all_items)}
        today = _today()

        # Validate every entity before writing any of them. A partial write on
        # a rejected batch leaves the graph in a state nobody asked for.
        for ent in entities:
            if not ent.get("name"):
                raise SchemaError("entity name is required")
            if ent["name"] not in existing:
                validate_entity_type(ent.get("entityType"))
                validate_domain(ent.get("domain"))
            validate_epistemic(ent.get("epistemic"))
            validate_visibility(ent.get("visibility"))

        created = []
        for ent in entities:
            name = ent["name"]
            new_obs_raw = ent.get("observations", [])

            if name in existing:
                old_obs = existing[name].get("observations", [])
                old_texts = {_obs_text(o) for o in old_obs}
                new_obs = []
                for o in new_obs_raw:
                    text = _obs_text(o)
                    if text not in old_texts:
                        new_obs.append({
                            "text": text,
                            "created": today,
                            "status": "active",
                        })
                existing[name]["observations"] = old_obs + new_obs
                # Merging into an entity that already exists: validate whatever
                # the caller supplied, but do not demand the new required
                # fields. The entity predates them; filling those in is the
                # migration's job, not this write's.
                if ent.get("entityType"):
                    existing[name]["entityType"] = validate_entity_type(ent["entityType"])
                if ent.get("domain"):
                    existing[name]["domain"] = validate_domain(ent["domain"])
                if "epistemic" in ent:
                    existing[name]["epistemic"] = validate_epistemic(ent["epistemic"])
                if "visibility" in ent:
                    existing[name]["visibility"] = validate_visibility(ent["visibility"])
                # Touch activation
                existing[name]["activation"] = 1.0
                existing[name]["last_accessed"] = today
                # Sync back
                for item in all_items:
                    if item.get("type") == "entity" and item.get("name") == name:
                        item.update(existing[name])
                        break
                created.append(existing[name])
            else:
                # Build temporal observations
                temporal_obs = []
                for o in new_obs_raw:
                    text = _obs_text(o)
                    temporal_obs.append({
                        "text": text,
                        "created": today,
                        "status": "active",
                    })
                new_entity = {
                    "type": "entity",
                    "name": name,
                    "entityType": validate_entity_type(ent.get("entityType")),
                    "domain": validate_domain(ent.get("domain")),
                    "epistemic": validate_epistemic(ent.get("epistemic")),
                    "visibility": validate_visibility(ent.get("visibility")),
                    "observations": temporal_obs,
                    "activation": 1.0,
                    "last_accessed": today,
                }
                all_items.append(new_entity)
                existing[name] = new_entity
                created.append(new_entity)

        self._write_all(all_items)
        return created

    @_serialized
    def create_relations(self, relations: list[dict]) -> list[dict]:
        """Create relations, skipping duplicates. Validates relation types against controlled vocabulary."""
        all_items = self._read_all()
        existing_rels = {
            (r["from"], r["to"], r["relationType"])
            for r in self._relations(all_items)
        }

        # Normalize and validate the whole batch first. Unknown types are
        # REJECTED, not accepted with a warning: the warning is what allowed 56
        # one-off relation types into a 165-relation graph.
        prepared = []
        for rel in relations:
            if not rel.get("from") or not rel.get("to"):
                raise SchemaError("relation requires both 'from' and 'to'")
            prepared.append((rel["from"], rel["to"],
                             normalize_relation_type(rel.get("relationType"))))

        created = []
        for src, dst, rel_type in prepared:
            key = (src, dst, rel_type)
            if key not in existing_rels:
                new_rel = {
                    "type": "relation",
                    "from": src,
                    "to": dst,
                    "relationType": rel_type,
                }
                all_items.append(new_rel)
                existing_rels.add(key)
                created.append(dict(new_rel))

        self._write_all(all_items)
        return created

    @_serialized
    def add_observations(self, observations: list[dict]) -> list[dict]:
        """Add observations to existing entities."""
        all_items = self._read_all()
        today = _today()
        results = []

        for obs in observations:
            entity_name = obs["entityName"]
            new_contents = obs.get("contents", [])
            for item in all_items:
                if item.get("type") == "entity" and item.get("name") == entity_name:
                    old = item.get("observations", [])
                    old_texts = {_obs_text(o) for o in old}
                    added = []
                    for c in new_contents:
                        text = _obs_text(c)
                        if text not in old_texts:
                            added.append({
                                "text": text,
                                "created": today,
                                "status": "active",
                            })
                    item["observations"] = old + added
                    # Touch activation
                    item["activation"] = 1.0
                    item["last_accessed"] = today
                    results.append({
                        "entityName": entity_name,
                        "addedObservations": [a["text"] for a in added],
                    })
                    break
            else:
                results.append({"entityName": entity_name, "error": "Entity not found"})

        self._write_all(all_items)
        return results

    @_serialized
    def invalidate_observations(self, entity_name: str, observations: list[str],
                                 superseded_by: str | None = None) -> dict:
        """Mark observations as invalidated with timestamp. Optionally note what superseded them."""
        all_items = self._read_all()
        today = _today()
        invalidated = []

        for item in all_items:
            if item.get("type") == "entity" and item.get("name") == entity_name:
                for obs in item.get("observations", []):
                    if _obs_text(obs) in observations:
                        obs["status"] = "invalidated"
                        obs["invalidated_at"] = today
                        if superseded_by:
                            obs["superseded_by"] = superseded_by
                        invalidated.append(_obs_text(obs))
                break

        if invalidated:
            self._write_all(all_items)

        return {"entityName": entity_name, "invalidated": invalidated}

    def delete_entities(self, names: list[str]) -> list[str]:
        """Delete entities and their relations."""
        with self._rlock, self._flock:
            all_items = self._read_all(force=True)
            existing = {e["name"] for e in self._entities(all_items)}
            names_set = set(names) & existing   # only report what was really there
            remaining = [
                item for item in all_items
                if not (
                    (item.get("type") == "entity" and item.get("name") in names_set)
                    or (item.get("type") == "relation" and (
                        item.get("from") in names_set or item.get("to") in names_set
                    ))
                )
            ]
            self._write_all(remaining)
        return sorted(names_set)

    @_serialized
    def delete_observations(self, deletions: list[dict]) -> list[dict]:
        """Delete specific observations from entities (matches on text field)."""
        all_items = self._read_all()
        results = []

        for deletion in deletions:
            entity_name = deletion["entityName"]
            to_remove = set(deletion.get("observations", []))
            for item in all_items:
                if item.get("type") == "entity" and item.get("name") == entity_name:
                    before = len(item.get("observations", []))
                    item["observations"] = [
                        o for o in item.get("observations", [])
                        if _obs_text(o) not in to_remove
                    ]
                    removed = before - len(item["observations"])
                    results.append({"entityName": entity_name, "removed": removed})
                    break

        self._write_all(all_items)
        return results

    @_serialized
    def delete_relations(self, relations: list[dict]) -> list[dict]:
        """Delete specific relations."""
        all_items = self._read_all()
        to_remove = {
            (r["from"], r["to"], r["relationType"]) for r in relations
        }

        remaining = [
            item for item in all_items
            if not (
                item.get("type") == "relation"
                and (item.get("from"), item.get("to"), item.get("relationType")) in to_remove
            )
        ]
        removed_count = len(all_items) - len(remaining)
        self._write_all(remaining)
        return [{"removed": removed_count}]

    # -----------------------------------------------------------------
    # Single write path
    #
    # Both writers go through record(): the session-close command extracting
    # from a transcript, and the phase pipeline closing a phase. Same
    # validation, same shape, one place to change. Different callers, one
    # operation.
    # -----------------------------------------------------------------

    @_serialized
    def record(self, entities: list[dict] | None = None,
               relations: list[dict] | None = None,
               observations: list[dict] | None = None) -> dict:
        """Validated, all-or-nothing write of entities, relations and observations.

        Everything is validated before anything is written. A batch that is
        half-valid writes nothing — a graph left in a state nobody asked for is
        worse than a rejected write.
        """
        entities = entities or []
        relations = relations or []
        observations = observations or []

        all_items = self._read_all()
        existing = {e["name"]: e for e in self._entities(all_items)}
        today = _today()

        # ---- validate everything first ----
        for ent in entities:
            if not ent.get("name"):
                raise SchemaError("entity name is required")
            if ent["name"] not in existing:
                validate_entity_type(ent.get("entityType"))
                validate_domain(ent.get("domain"))
            validate_epistemic(ent.get("epistemic"))
            validate_visibility(ent.get("visibility"))
            for o in ent.get("observations", []):
                if isinstance(o, dict):
                    validate_observation_kind(o.get("kind"))

        prepared_rels = []
        for rel in relations:
            if not rel.get("from") or not rel.get("to"):
                raise SchemaError("relation requires both 'from' and 'to'")
            prepared_rels.append((rel["from"], rel["to"],
                                  normalize_relation_type(rel.get("relationType"))))

        for obs in observations:
            if not obs.get("entityName"):
                raise SchemaError("observation requires 'entityName'")
            validate_observation_kind(obs.get("kind"))

        # A relation must not point at a name that neither exists nor is being
        # created in this same call. Dangling edges are how a graph stops being
        # traversable without anyone noticing.
        will_exist = set(existing) | {e["name"] for e in entities}
        dangling = {n for src, dst, _ in prepared_rels for n in (src, dst)
                    if n not in will_exist}
        if dangling:
            raise SchemaError(
                f"relation points at unknown entities: {_fmt(dangling)}. "
                f"Create them in the same record() call, or fix the name."
            )

        # ---- write ----
        created_entities, created_relations, added_observations = [], [], []

        for ent in entities:
            name = ent["name"]
            new_obs = []
            for o in ent.get("observations", []):
                text = _obs_text(o)
                kind = validate_observation_kind(o.get("kind") if isinstance(o, dict) else None)
                new_obs.append({"text": text, "kind": kind,
                                "created": today, "status": "active"})
            if name in existing:
                old = existing[name].get("observations", [])
                old_texts = {_obs_text(o) for o in old}
                existing[name]["observations"] = old + [
                    o for o in new_obs if o["text"] not in old_texts
                ]
                if ent.get("entityType"):
                    existing[name]["entityType"] = validate_entity_type(ent["entityType"])
                if ent.get("domain"):
                    existing[name]["domain"] = validate_domain(ent["domain"])
                if "epistemic" in ent:
                    existing[name]["epistemic"] = validate_epistemic(ent["epistemic"])
                if "visibility" in ent:
                    existing[name]["visibility"] = validate_visibility(ent["visibility"])
                existing[name]["activation"] = 1.0
                existing[name]["last_accessed"] = today
                for item in all_items:
                    if item.get("type") == "entity" and item.get("name") == name:
                        item.update(existing[name])
                        break
                created_entities.append(existing[name])
            else:
                new_entity = {
                    "type": "entity",
                    "name": name,
                    "entityType": validate_entity_type(ent.get("entityType")),
                    "domain": validate_domain(ent.get("domain")),
                    "epistemic": validate_epistemic(ent.get("epistemic")),
                    "visibility": validate_visibility(ent.get("visibility")),
                    "observations": new_obs,
                    "activation": 1.0,
                    "last_accessed": today,
                }
                all_items.append(new_entity)
                existing[name] = new_entity
                created_entities.append(new_entity)

        existing_rels = {(r["from"], r["to"], r["relationType"])
                         for r in self._relations(all_items)}
        for src, dst, rel_type in prepared_rels:
            key = (src, dst, rel_type)
            if key not in existing_rels:
                new_rel = {"type": "relation", "from": src, "to": dst,
                           "relationType": rel_type}
                all_items.append(new_rel)
                existing_rels.add(key)
                created_relations.append(new_rel)

        for obs in observations:
            name = obs["entityName"]
            kind = validate_observation_kind(obs.get("kind"))
            target = next((i for i in all_items
                           if i.get("type") == "entity" and i.get("name") == name), None)
            if target is None:
                raise SchemaError(f"observation targets unknown entity '{name}'")
            old_texts = {_obs_text(o) for o in target.get("observations", [])}
            for text in obs.get("contents", []):
                if text not in old_texts:
                    target.setdefault("observations", []).append(
                        {"text": text, "kind": kind, "created": today, "status": "active"})
                    added_observations.append({"entityName": name, "text": text, "kind": kind})

        self._write_all(all_items)
        return {
            "entities": created_entities,
            "relations": created_relations,
            "observations": added_observations,
        }

    def record_decision(self, name: str, domain: str, decided: str,
                        because: str, rejected: list[str] | None = None,
                        touches: list[str] | None = None,
                        epistemic: str = "assertion",
                        visibility: str | None = None,
                        supersedes: str | None = None,
                        relations: list[dict] | None = None) -> dict:
        """Convenience builder over record() for the decision shape.

        Not a second write path — it assembles arguments and calls record().
        `rejected` is the field that earns this its own helper: what was chosen
        is recoverable from the code, what was considered and dropped is not.
        """
        obs = [{"text": decided, "kind": "decided"},
               {"text": because, "kind": "because"}]
        obs += [{"text": r, "kind": "rejected"} for r in (rejected or [])]
        obs += [{"text": t, "kind": "touches"} for t in (touches or [])]

        rels = list(relations or [])
        if supersedes:
            rels.append({"from": name, "to": supersedes, "relationType": "supersedes"})

        return self.record(
            entities=[{"name": name, "entityType": "decision", "domain": domain,
                       "epistemic": epistemic, "visibility": visibility,
                       "observations": obs}],
            relations=rels,
        )

    def revise(self, entity_name: str, observations: list[str],
               superseded_by: str | None = None,
               superseding_entity: str | None = None) -> dict:
        """Mark observations invalidated, optionally linking what replaced them.

        Deliberately not a delete. The value of a decision archive is that it
        remembers what you used to believe and when you stopped; removing the
        old belief destroys exactly the thing that makes the record worth
        keeping.
        """
        result = self.invalidate_observations(entity_name, observations, superseded_by)
        if superseding_entity and result.get("invalidated"):
            self.record(relations=[{"from": superseding_entity, "to": entity_name,
                                    "relationType": "supersedes"}])
            result["supersedes_edge"] = f"{superseding_entity} -> {entity_name}"
        return result

    def close(self) -> None:
        """Release the inter-process lock if this process still holds it."""
        try:
            if self._flock.is_locked:
                self._flock.release(force=True)
        except Exception:
            pass

    def read_graph(self) -> dict:
        """Return all entities and relations, with stored activation as-is.

        Decay is applied where activation is *used* for ranking — search_nodes
        sorts by it, and the summary view in the server reports the decayed
        value. This returns the raw record, so a caller reading the graph sees
        the same numbers that are on disk. The module docstring used to imply
        decay was applied here; it was not, and the honest fix was to say so
        rather than to add a transformation nobody asked for.
        """
        all_items = self._read_all()
        return {
            "entities": self._entities(all_items),
            "relations": self._relations(all_items),
        }

    def open_nodes(self, names: list[str]) -> dict:
        """Return specific entities and their relations. Pure read.

        This used to write on the read path to refresh activation, which meant
        every read produced a diff — and the file is meant to be git-diffable,
        so reading it defeated the point. Activation is refreshed on write
        instead; reading something is weaker evidence of relevance than
        recording something about it.
        """
        all_items = self._read_all()
        names_set = set(names)

        entities = [
            e for e in self._entities(all_items) if e["name"] in names_set
        ]
        relations = [
            r for r in self._relations(all_items)
            if r.get("from") in names_set or r.get("to") in names_set
        ]
        return {"entities": entities, "relations": relations}
