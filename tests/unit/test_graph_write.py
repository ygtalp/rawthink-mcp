"""Write-path regression locks: atomicity, dangling edges, belief revision."""
import pytest

from rawthink_mcp.graph import SchemaError


def _entities(kg):
    return {e["name"]: e for e in kg.read_graph()["entities"]}


class TestRecord:
    def test_writes_entities_relations_and_observations_together(self, kg):
        r = kg.record(
            entities=[{"name": "a", "entityType": "decision", "domain": "software",
                       "observations": ["chose a"]},
                      {"name": "b", "entityType": "concept", "domain": "software"}],
            relations=[{"from": "a", "to": "b", "relationType": "depends_on"}])
        assert len(r["entities"]) == 2 and len(r["relations"]) == 1

    def test_rejected_batch_writes_nothing(self, kg):
        """Half a graph is worse than none — the caller cannot tell which half."""
        kg.record(entities=[{"name": "seed", "entityType": "concept", "domain": "music"}])
        before = len(_entities(kg))
        with pytest.raises(SchemaError):
            kg.record(entities=[
                {"name": "good", "entityType": "concept", "domain": "music"},
                {"name": "bad", "entityType": "NOT-A-TYPE", "domain": "music"}])
        assert len(_entities(kg)) == before
        assert "good" not in _entities(kg)

    def test_relation_to_unknown_entity_is_rejected(self, kg):
        """Dangling edges are invisible: the edge simply never traverses."""
        kg.record(entities=[{"name": "a", "entityType": "concept", "domain": "software"}])
        with pytest.raises(SchemaError, match="unknown entities"):
            kg.record(relations=[{"from": "a", "to": "ghost",
                                  "relationType": "related_to"}])

    def test_relation_to_entity_created_in_same_call_is_allowed(self, kg):
        r = kg.record(
            entities=[{"name": "a", "entityType": "concept", "domain": "software"},
                      {"name": "b", "entityType": "concept", "domain": "software"}],
            relations=[{"from": "a", "to": "b", "relationType": "related_to"}])
        assert len(r["relations"]) == 1

    def test_merging_into_existing_entity_does_not_demand_new_fields(self, kg):
        """Pre-migration entities must stay writable."""
        kg.record(entities=[{"name": "old", "entityType": "concept", "domain": "software"}])
        r = kg.record(entities=[{"name": "old", "observations": ["later note"]}])
        assert r["entities"][0]["name"] == "old"

    def test_defaults_are_applied(self, kg):
        kg.record(entities=[{"name": "x", "entityType": "insight", "domain": "music"}])
        e = _entities(kg)["x"]
        assert e["epistemic"] == "unknown" and e["visibility"] == "private"


class TestRecordDecision:
    def test_rejected_alternatives_are_queryable(self, kg):
        """The reason this helper exists: what was chosen stays readable in the
        code, what was dropped exists nowhere else."""
        kg.record_decision(name="d1", domain="software", decided="use UUID",
                           because="no cross-service sequence",
                           rejected=["db sequence", "millis+random"],
                           touches=["a.py"])
        obs = _entities(kg)["d1"]["observations"]
        kinds = {}
        for o in obs:
            kinds[o["kind"]] = kinds.get(o["kind"], 0) + 1
        assert kinds == {"decided": 1, "because": 1, "rejected": 2, "touches": 1}
        rejected = [o["text"] for o in obs if o["kind"] == "rejected"]
        assert len(rejected) == 2

    def test_records_as_decision_type(self, kg):
        kg.record_decision(name="d2", domain="music", decided="x", because="y")
        assert _entities(kg)["d2"]["entityType"] == "decision"


class TestRevise:
    def test_invalidates_without_deleting(self, kg):
        kg.record_decision(name="d", domain="software", decided="old way",
                           because="it was fine then")
        kg.revise("d", ["old way"], superseded_by="new way")
        obs = _entities(kg)["d"]["observations"]
        invalid = [o for o in obs if o.get("status") == "invalidated"]
        assert len(invalid) == 1
        assert invalid[0]["superseded_by"] == "new way"
        assert "invalidated_at" in invalid[0]
        assert any(o.get("status") == "active" for o in obs), \
            "the rest of the decision must survive"

    def test_links_the_superseding_entity(self, kg):
        kg.record_decision(name="old", domain="software", decided="a", because="b")
        kg.record_decision(name="new", domain="software", decided="c", because="d")
        kg.revise("old", ["a"], superseded_by="c", superseding_entity="new")
        rels = kg.read_graph()["relations"]
        assert any(r["from"] == "new" and r["to"] == "old"
                   and r["relationType"] == "supersedes" for r in rels)


class TestBoundedSearch:
    def test_applies_a_limit_and_reports_the_total(self, kg):
        kg.record(entities=[{"name": f"node-{i}", "entityType": "concept",
                             "domain": "software", "observations": ["shared token"]}
                            for i in range(25)])
        r = kg.search_nodes("shared", limit=5)
        assert len(r["entities"]) == 5
        assert r["total_matched"] == 25
        assert r["truncated"] is True

    def test_filters_by_domain_and_type(self, kg):
        kg.record(entities=[
            {"name": "s1", "entityType": "decision", "domain": "software",
             "observations": ["token"]},
            {"name": "m1", "entityType": "concept", "domain": "music",
             "observations": ["token"]}])
        assert kg.search_nodes("token", domain="music")["total_matched"] == 1
        assert kg.search_nodes("token", entity_type="decision")["total_matched"] == 1
