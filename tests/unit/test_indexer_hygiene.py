"""Phase 4 regression locks: state location, qnote identity, error paths, paging."""

from pathlib import Path

import pytest

from rawthink_mcp import config
from rawthink_mcp.chunker import ThoughtChunk
from rawthink_mcp.indexer import EmbeddingError, Indexer


class TestBM25StateLocation:
    def test_state_path_defaults_to_the_vault(self):
        """It used to live in the package directory, so two vaults on one
        machine shared a single IDF table and a read-only install could not
        write it at all."""
        assert ".bm25_state.json" in config.BM25_STATE_FILE
        assert "site-packages" not in config.BM25_STATE_FILE

    def test_path_is_a_constructor_argument(self, tmp_path, stub_embedder):
        p = tmp_path / "custom" / "state.json"
        idx = Indexer(qdrant_path=":memory:", embedder=stub_embedder,
                      bm25_state_path=str(p))
        assert idx._bm25_path == str(p)

    def test_constructing_does_not_create_directories(self, tmp_path, stub_embedder):
        """Building an Indexer in a test must not touch the real vault."""
        target = tmp_path / "should-not-exist"
        Indexer(qdrant_path=":memory:", embedder=stub_embedder,
                bm25_state_path=str(target / "state.json"))
        assert not target.exists()

    def test_initialize_removes_package_directory_state(self, tmp_path, stub_embedder, caplog):
        legacy = tmp_path / "legacy" / ".bm25_state.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("{}")
        idx = Indexer(qdrant_path=":memory:", embedder=stub_embedder,
                      bm25_state_path=str(tmp_path / "vault" / "state.json"))
        idx._legacy_bm25_path = str(legacy)
        idx.initialize()
        assert not legacy.exists()


class TestQnoteIdentity:
    def test_chunk_carries_session_ref_into_the_payload(self):
        """session_ref reached the frontmatter but never the payload, so a
        qnote could not be traced back to the session that produced it."""
        c = ThoughtChunk(session_id="qnote_x", title="t", date="2026-01-01",
                         tags=[], section_heading="", chunk_text="body",
                         chunk_index=0, line_start=1, line_end=1,
                         content_hash="h", source_type="qnote",
                         session_ref="2026-01-01_001")
        assert c.to_payload()["session_ref"] == "2026-01-01_001"

    def test_session_ref_defaults_to_empty(self):
        c = ThoughtChunk(session_id="s", title="t", date="d", tags=[],
                         section_heading="", chunk_text="b", chunk_index=0,
                         line_start=1, line_end=1, content_hash="h",
                         source_type="session")
        assert c.to_payload()["session_ref"] == ""


class TestPayloadIndexes:
    def test_indexes_are_created_for_an_existing_collection(self, vault, stub_embedder):
        """The loop used to sit inside the create-collection branch, so a
        collection made before a field existed never gained its index."""
        idx = Indexer(qdrant_path=":memory:", embedder=stub_embedder,
                      bm25_state_path=str(vault / "state.json"))
        idx.initialize()
        idx.initialize()   # second run: collection already exists
        assert idx._client is not None


class TestErrorPaths:
    def test_timeout_is_caught_not_leaked(self, vault, monkeypatch):
        """Timeouts are httpx.HTTPError, not ConnectError, and used to escape
        as a raw exception instead of reaching the cache fallback."""
        import httpx
        import rawthink_mcp.indexer as mod

        def boom(*a, **k):
            raise httpx.ReadTimeout("too slow")

        monkeypatch.setattr(mod.ollama, "embed", boom)
        idx = Indexer(qdrant_path=":memory:",
                      bm25_state_path=str(vault / "state.json"))
        with pytest.raises(EmbeddingError):
            idx._embed("anything")

    def test_empty_sparse_vector_is_detected(self):
        class Empty:
            indices = []
        class Full:
            indices = [1, 2]
        assert Indexer._has_sparse_terms(Empty()) is False
        assert Indexer._has_sparse_terms(Full()) is True


class TestPagination:
    def test_get_session_chunks_returns_more_than_one_page(self, indexer, vault):
        """A flat limit=1000 truncated long sessions silently."""
        chunks = [ThoughtChunk(session_id="big", title="Big", date="2026-01-01",
                               tags=[], section_heading="", chunk_text=f"body {i}",
                               chunk_index=i, line_start=i, line_end=i,
                               content_hash=f"h{i}", source_type="session")
                  for i in range(300)]
        for c in chunks:
            indexer.index_chunk(c)
        got = indexer.get_session_chunks("big")
        assert len(got) == 300, f"paged read returned {len(got)}/300"
        assert [c["chunk_index"] for c in got] == list(range(300))


class TestNoRepoWrites:
    """Guard against the leak that actually happened.

    A test run wrote a BM25 state file into the repo's own vault directory,
    because the fixture let the Indexer fall back to config.BM25_STATE_FILE.
    Nothing failed; the file just appeared in `git status` days later.
    """

    def test_fixture_writes_only_under_tmp(self, indexer, vault, session_file, tmp_path):
        from rawthink_mcp import config
        repo_state = Path(config.BM25_STATE_FILE)
        before = repo_state.exists()
        session_file("2026-01-01_001", "Leak", "content")
        indexer.index_vault(force=True)
        assert repo_state.exists() == before, \
            f"the test wrote to {repo_state}, outside its tmp directory"
        assert Path(indexer._bm25_path).is_relative_to(tmp_path)
