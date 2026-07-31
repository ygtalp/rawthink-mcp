"""Migration regression locks.

The migration is a one-off, which is exactly why it needs tests: it runs once,
against data that cannot be regenerated, and a mistake is discovered later.
"""
import json

from rawthink_mcp import config
from rawthink_mcp.migrate import heal_dangling, migrate_items


def _legacy_graph():
    return [
        {"type": "entity", "name": "karar-1", "entityType": "karar",
         "observations": [{"text": "an old note", "status": "active"}],
         "activation": 1.0, "last_accessed": "2026-01-01"},
        {"type": "entity", "name": "saglik-1", "entityType": "saglik-bulgusu",
         "observations": [{"text": "n", "status": "active"}],
         "activation": 1.0, "last_accessed": "2026-01-01"},
        {"type": "entity", "name": "kavram-1", "entityType": "kavram",
         "observations": [], "activation": 1.0, "last_accessed": "2026-01-01"},
        {"type": "relation", "from": "karar-1", "to": "kavram-1",
         "relationType": "connected_to"},
        {"type": "relation", "from": "karar-1", "to": "saglik-1",
         "relationType": "beyin-entropisini-artirir"},
    ]


class TestTypeMapping:
    def test_maps_legacy_types_onto_the_closed_list(self):
        out, rep = migrate_items(_legacy_graph())
        types = {e["entityType"] for e in out if e.get("type") == "entity"}
        assert types <= config.ENTITY_TYPES
        assert not rep["type_unmapped"]

    def test_infers_domain_only_where_the_old_type_carried_it(self):
        out, rep = migrate_items(_legacy_graph())
        by = {e["name"]: e for e in out if e.get("type") == "entity"}
        assert by["saglik-1"]["domain"] == "health", "health was implied by the type"
        assert "domain" not in by["karar-1"], "must not invent a domain it cannot know"

    def test_backfills_epistemic_as_unknown_not_assertion(self):
        out, _ = migrate_items(_legacy_graph())
        assert all(e["epistemic"] == "unknown"
                   for e in out if e.get("type") == "entity")


class TestRelationMapping:
    def test_folds_and_preserves_the_original(self):
        out, _ = migrate_items(_legacy_graph())
        rels = [r for r in out if r.get("type") == "relation"]
        assert {r["relationType"] for r in rels} <= config.RELATION_TYPES
        folded = [r for r in rels if "original_type" in r]
        assert folded, "nothing should be lost — the original string is kept"
        assert any(r["original_type"] == "beyin-entropisini-artirir" for r in folded)


class TestIdempotency:
    def test_running_twice_changes_nothing_the_second_time(self):
        once, _ = migrate_items(_legacy_graph())
        twice, rep = migrate_items(json.loads(json.dumps(once)))
        assert once == twice
        assert sum(rep["type_changed"].values()) == 0
        assert rep["epistemic_backfilled"] == 0


class TestDanglingRepair:
    def test_repoints_a_rename(self):
        items = [
            {"type": "entity", "name": "rule-3-non-duality", "entityType": "rule",
             "observations": [], "activation": 1.0, "last_accessed": "2026-01-01"},
            {"type": "relation", "from": "rule-3-non-duality", "to": "rule-3",
             "relationType": "related_to"},
        ]
        rep = heal_dangling(items)
        assert rep["repointed"] and not rep["stubbed"]
        rel = [i for i in items if i.get("type") == "relation"][0]
        assert rel["to"] == "rule-3-non-duality"
        assert rel["repointed_from"] == "rule-3"

    def test_stubs_when_there_is_no_rename_candidate(self):
        items = [
            {"type": "entity", "name": "a", "entityType": "concept",
             "observations": [], "activation": 1.0, "last_accessed": "2026-01-01"},
            {"type": "relation", "from": "a", "to": "project:something",
             "relationType": "part_of"},
        ]
        rep = heal_dangling(items)
        assert rep["stubbed"] and not rep["repointed"]
        stub = [i for i in items if i.get("name") == "project:something"][0]
        assert stub["reconstructed"] is True
        assert stub["epistemic"] == "unknown"
        assert stub["entityType"] in config.ENTITY_TYPES

    def test_leaves_no_dangling_edges(self):
        items = [
            {"type": "entity", "name": "a", "entityType": "concept",
             "observations": [], "activation": 1.0, "last_accessed": "2026-01-01"},
            {"type": "relation", "from": "a", "to": "ghost", "relationType": "related_to"},
        ]
        heal_dangling(items)
        names = {i["name"] for i in items if i.get("type") == "entity"}
        dangling = {n for r in items if r.get("type") == "relation"
                    for n in (r["from"], r["to"]) if n not in names}
        assert not dangling
