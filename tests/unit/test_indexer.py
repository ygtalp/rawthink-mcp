"""Indexer regression locks.

Covers the stdio contract (A2) and the batch truncation that lost chunks
without reporting anything (A18).
"""
import json

import pytest

from rawthink_mcp.indexer import EmbeddingError


def test_index_vault_writes_nothing_to_stdout(indexer, vault, session_file, capsys):
    """stdout is the JSON-RPC channel. A single stray line corrupts the
    protocol, and nothing on this side reports it."""
    session_file("2026-01-01_001", "Quiet", "Some content.")
    indexer.index_vault()
    assert capsys.readouterr().out == ""


def test_empty_vault_writes_nothing_to_stdout(indexer, capsys):
    indexer.index_vault()
    assert capsys.readouterr().out == ""


def test_batch_length_mismatch_raises(vault, broken_embedder):
    """A short embeddings list used to be zipped silently, leaving some chunks
    unindexed with no error anywhere."""
    from rawthink_mcp.indexer import Indexer
    idx = Indexer(qdrant_path=":memory:", embedder=broken_embedder)
    with pytest.raises(EmbeddingError, match="vectors for"):
        idx._embed_batch(["one", "two", "three"])


def test_incompatible_bm25_state_is_discarded(vault, tmp_path, stub_embedder, caplog):
    """A legacy state file must be removed, not silently kept."""
    from rawthink_mcp.indexer import Indexer
    legacy = tmp_path / ".bm25_state.json"
    legacy.write_text(json.dumps({"k1": 1.2, "b": 0.75, "vocab": {"a": 0},
                                  "idf": {"a": 1.0}, "avgdl": 3.0, "n_docs": 1}))
    idx = Indexer(qdrant_path=":memory:", embedder=stub_embedder)
    idx._bm25_path = str(legacy)
    idx.initialize()
    assert not legacy.exists(), "legacy state should have been deleted"
    assert any("BM25" in r.message or "BM25" in r.getMessage() for r in caplog.records)


def test_force_reindex_reencodes_unchanged_chunks(indexer, vault, session_file):
    """`full=True` used to still pass skip_unchanged=True, so a "full" reindex
    left stale encodings in place — which is how a corrupt sparse index
    survives the documented remedy."""
    session_file("2026-01-01_001", "Force", "content that will not change")
    first = indexer.index_vault()
    skipped = indexer.index_vault()            # nothing changed
    forced = indexer.index_vault(force=True)   # must re-encode anyway
    assert first > 0
    assert forced >= first, "force=True must not skip unchanged chunks"
    assert forced > skipped or skipped == 0
