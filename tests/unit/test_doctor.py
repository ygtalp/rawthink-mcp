"""Doctor regression locks.

The checks exist because each of these failures is quiet: an index that
stopped halfway, a state file from before the term-ID fix, a collection built
with a different model. Nothing raises; search just gets worse. If a check
stops catching its case it has to fail here, not in six months.
"""
import json

from rawthink_mcp import doctor
from rawthink_mcp.doctor import FAIL, OK, WARN, Report


def _statuses(rep):
    return {name: status for status, name, _d, _f in rep.rows}


class TestVault:
    def test_reports_counts_for_a_healthy_vault(self, vault, session_file):
        session_file("2026-01-01_001", "A", "body")
        rep = Report(); doctor.check_vault(rep, vault)
        assert _statuses(rep)["vault directory"] == OK

    def test_fails_when_missing(self, tmp_path):
        rep = Report(); doctor.check_vault(rep, tmp_path / "nope")
        assert _statuses(rep)["vault directory"] == FAIL


class TestGraph:
    def test_warns_on_a_fresh_install(self, tmp_path):
        rep = Report(); doctor.check_graph(rep, tmp_path / "memory.jsonl")
        assert _statuses(rep)["knowledge graph"] == WARN

    def test_passes_on_a_migrated_graph(self, vault, kg):
        kg.record(entities=[{"name": "a", "entityType": "concept", "domain": "music"}])
        rep = Report(); doctor.check_graph(rep, vault / "memory.jsonl")
        assert _statuses(rep)["knowledge graph"] == OK

    def test_fails_on_pre_migration_types(self, tmp_path):
        """The 46-type era. Search cannot filter, and writes are rejected."""
        p = tmp_path / "memory.jsonl"
        p.write_text(json.dumps({"type": "entity", "name": "x",
                                 "entityType": "karar", "observations": []}) + "\n")
        rep = Report(); doctor.check_graph(rep, p)
        assert _statuses(rep)["graph schema"] == FAIL

    def test_warns_on_dangling_edges(self, tmp_path):
        p = tmp_path / "memory.jsonl"
        p.write_text("\n".join([
            json.dumps({"type": "entity", "name": "a", "entityType": "concept",
                        "observations": []}),
            json.dumps({"type": "relation", "from": "a", "to": "ghost",
                        "relationType": "related_to"})]) + "\n")
        rep = Report(); doctor.check_graph(rep, p)
        assert _statuses(rep)["graph edges"] == WARN

    def test_fails_on_unparseable_lines(self, tmp_path):
        p = tmp_path / "memory.jsonl"
        p.write_text('{"type": "entity"\nnot json at all\n')
        rep = Report(); doctor.check_graph(rep, p)
        assert _statuses(rep)["graph file"] == FAIL


class TestBM25State:
    def test_fails_on_legacy_format(self, tmp_path):
        """The exact condition found on a real machine: format_version=1 means
        corpus-dependent term IDs, so sparse vectors no longer match."""
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"k1": 1.2, "b": 0.75, "vocab": {"a": 0},
                                 "idf": {}, "avgdl": 1.0, "n_docs": 1}))
        rep = Report(); doctor.check_bm25_state(rep, p)
        status = _statuses(rep)["BM25 state"]
        assert status == FAIL
        assert any("reindex" in fix for *_x, fix in rep.rows), "must say how to fix it"

    def test_passes_on_current_format(self, tmp_path):
        from rawthink_mcp.sparse import BM25Tokenizer
        t = BM25Tokenizer(); t.fit(["one two three"])
        p = tmp_path / "state.json"; t.save(str(p))
        rep = Report(); doctor.check_bm25_state(rep, p)
        assert _statuses(rep)["BM25 state"] == OK

    def test_warns_when_not_built_yet(self, tmp_path):
        rep = Report(); doctor.check_bm25_state(rep, tmp_path / "absent.json")
        assert _statuses(rep)["BM25 state"] == WARN


class TestIndexCoverage:
    """The check that was missing. A half-finished index looks healthy: the
    collection exists, it has points, queries return results — from half the
    vault, with nothing saying so."""

    def test_detects_sessions_that_were_never_indexed(self, indexer, vault, session_file):
        session_file("2026-01-01_001", "Indexed", "this one gets indexed")
        indexer.index_vault()
        session_file("2026-02-02_002", "Missing", "written after the index ran")

        rep = Report()
        doctor.check_collection(rep, indexer._client, vault)
        assert _statuses(rep)["index coverage"] == FAIL
        detail = next(d for s, n, d, _f in rep.rows if n == "index coverage")
        assert "2026-02-02_002" in detail

    def test_passes_when_everything_is_indexed(self, indexer, vault, session_file):
        session_file("2026-01-01_001", "A", "body one")
        session_file("2026-01-02_002", "B", "body two")
        indexer.index_vault()
        rep = Report()
        doctor.check_collection(rep, indexer._client, vault)
        assert _statuses(rep)["index coverage"] == OK

    def test_is_a_noop_without_a_client(self, vault):
        rep = Report(); doctor.check_collection(rep, None, vault)
        assert rep.rows == []


class TestReport:
    def test_exit_code_is_nonzero_only_on_failure(self, capsys):
        rep = Report(); rep.add(OK, "a"); rep.add(WARN, "b")
        assert rep.render() == 0
        rep.add(FAIL, "c", fix="do the thing")
        assert rep.render() == 1

    def test_fix_hint_is_printed_for_failures_only(self, capsys):
        rep = Report()
        rep.add(OK, "fine", fix="should not appear")
        rep.add(FAIL, "broken", fix="should appear")
        rep.render()
        out = capsys.readouterr().out
        assert "should appear" in out and "should not appear" not in out


class TestVaultArgumentPropagation:
    """`--vault` has to move every vault-derived path with it.

    It moved the vault and the graph but not the BM25 state, which is computed
    at import time from the default. The answer looked complete and was half
    wrong — the right vault checked against a state file belonging to another.
    """

    def test_state_is_looked_up_inside_the_given_vault(self, tmp_path, monkeypatch):
        from rawthink_mcp.sparse import BM25Tokenizer
        vault = tmp_path / "real"
        (vault / "sessions").mkdir(parents=True)
        (vault / "sessions" / "2026-01-01_001_x.md").write_text("# x", encoding="utf-8")
        t = BM25Tokenizer(); t.fit(["one two three"])
        t.save(str(vault / ".bm25_state.json"))

        monkeypatch.delenv("RAWTHINK_BM25_STATE", raising=False)
        monkeypatch.setattr("sys.argv", ["rawthink-doctor", "--vault", str(vault)])
        doctor.main(["--vault", str(vault)])   # must not raise

        rep = Report()
        doctor.check_bm25_state(rep, vault / ".bm25_state.json", vault)
        assert _statuses(rep)["BM25 state"] == OK

    def test_populated_state_with_an_empty_vault_is_flagged(self, tmp_path):
        from rawthink_mcp.sparse import BM25Tokenizer
        vault = tmp_path / "empty"
        (vault / "sessions").mkdir(parents=True)
        t = BM25Tokenizer(); t.fit(["one two three"])
        p = tmp_path / "state.json"; t.save(str(p))
        rep = Report(); doctor.check_bm25_state(rep, p, vault)
        assert _statuses(rep)["BM25 state"] == WARN
