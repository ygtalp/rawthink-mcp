"""JSONL-backed knowledge graph with Turkish character normalization.

Drop-in replacement for @modelcontextprotocol/server-memory graph operations.
Reads/writes the same memory.jsonl format.

Tier 1: Atomic writes, temporal metadata on observations, epistemic typing,
        controlled relation vocabulary.
Tier 2: Activation decay, in-memory inverted index for search.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime
from pathlib import Path

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

def _normalize_observation(obs) -> dict:
    """Normalize a legacy string observation into the temporal dict format."""
    if isinstance(obs, str):
        return {"text": obs, "status": "active"}
    return obs


def _obs_text(obs) -> str:
    """Extract text from an observation (dict or legacy string)."""
    if isinstance(obs, dict):
        return obs.get("text", "")
    return str(obs)


# ---------------------------------------------------------------------------
# Knowledge Graph
# ---------------------------------------------------------------------------

class KnowledgeGraph:
    """JSONL-backed knowledge graph with Turkish-aware search."""

    def __init__(self, path: str | None = None):
        self._path = Path(path or config.MEMORY_FILE).resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, set[str]] | None = None  # token -> entity names
        self._cached_items: list[dict] | None = None

    # --- internal I/O ---

    def _read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
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
        self._build_index(items)
        return items

    def _write_all(self, items: list[dict]) -> None:
        """Atomic write: write to .tmp then os.replace()."""
        text = "\n".join(json.dumps(item, ensure_ascii=False) for item in items)
        tmp = self._path.with_suffix(".jsonl.tmp")
        tmp.write_text(text + "\n", encoding="utf-8")
        os.replace(str(tmp), str(self._path))
        # Invalidate caches
        self._cached_items = None
        self._index = None

    def _entities(self, items: list[dict] | None = None) -> list[dict]:
        return [i for i in (items or self._read_all()) if i.get("type") == "entity"]

    def _relations(self, items: list[dict] | None = None) -> list[dict]:
        return [i for i in (items or self._read_all()) if i.get("type") == "relation"]

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

    def search_nodes(self, query: str) -> dict:
        """Search entities by name, type, and observations with Turkish normalization."""
        all_items = self._read_all()
        nq = normalize_turkish(query)
        tokens = nq.split()

        # Use inverted index for fast lookup with token match scoring
        # Each candidate gets a score = number of query tokens matched
        candidate_scores: dict[str, int] = {}
        if self._index and tokens:
            for tok in tokens:
                matched_names: set[str] = set()
                for idx_tok, names in self._index.items():
                    # Exact match always; prefix/substring only for tokens >= 3 chars
                    if tok == idx_tok or (len(tok) >= 3 and tok in idx_tok):
                        matched_names.update(names)
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

        relations = [
            r for r in self._relations(all_items)
            if r.get("from") in candidate_names or r.get("to") in candidate_names
        ]
        return {"entities": entities, "relations": relations}

    def create_entities(self, entities: list[dict]) -> list[dict]:
        """Create entities, merging observations if name already exists."""
        all_items = self._read_all()
        existing = {e["name"]: e for e in self._entities(all_items)}
        today = _today()

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
                # Update epistemic if provided
                if "epistemic" in ent:
                    existing[name]["epistemic"] = ent["epistemic"]
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
                    "entityType": ent.get("entityType", "concept"),
                    "observations": temporal_obs,
                    "activation": 1.0,
                    "last_accessed": today,
                }
                if "epistemic" in ent:
                    new_entity["epistemic"] = ent["epistemic"]
                all_items.append(new_entity)
                existing[name] = new_entity
                created.append(new_entity)

        self._write_all(all_items)
        return created

    def create_relations(self, relations: list[dict]) -> list[dict]:
        """Create relations, skipping duplicates. Validates relation types against controlled vocabulary."""
        all_items = self._read_all()
        existing_rels = {
            (r["from"], r["to"], r["relationType"])
            for r in self._relations(all_items)
        }

        created = []
        for rel in relations:
            rel_type = rel["relationType"]
            key = (rel["from"], rel["to"], rel_type)

            # Controlled vocabulary check
            warning = None
            if rel_type not in config.RELATION_TYPES:
                warning = (
                    f"Non-standard relationType '{rel_type}'. "
                    f"Canonical types: {', '.join(sorted(config.RELATION_TYPES))}"
                )

            if key not in existing_rels:
                new_rel = {
                    "type": "relation",
                    "from": rel["from"],
                    "to": rel["to"],
                    "relationType": rel_type,
                }
                all_items.append(new_rel)
                existing_rels.add(key)
                result = dict(new_rel)
                if warning:
                    result["warning"] = warning
                created.append(result)

        self._write_all(all_items)
        return created

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
        all_items = self._read_all()
        names_set = set(names)
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
        return list(names_set)

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

    def read_graph(self) -> dict:
        """Return all entities and relations."""
        all_items = self._read_all()
        return {
            "entities": self._entities(all_items),
            "relations": self._relations(all_items),
        }

    def open_nodes(self, names: list[str]) -> dict:
        """Return specific entities and their relations. Touches activation."""
        all_items = self._read_all()
        names_set = set(names)

        # Touch activation on accessed entities
        if self._touch_entities(names_set, all_items):
            self._write_all(all_items)
            all_items = self._read_all()

        entities = [
            e for e in self._entities(all_items) if e["name"] in names_set
        ]
        relations = [
            r for r in self._relations(all_items)
            if r.get("from") in names_set or r.get("to") in names_set
        ]
        return {"entities": entities, "relations": relations}
