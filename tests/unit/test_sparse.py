"""BM25 regression locks (A1).

The defect these guard: term IDs were positions in a sorted vocabulary, so one
new token sorting early shifted every ID after it. Vectors written before a
reindex stopped matching queries encoded after it — silently, because RRF kept
working off the dense side. Measured on a real vault: sparse MRR 0.16 while
the fused result looked healthy.
"""
import json

import pytest

from rawthink_mcp.sparse import BM25Tokenizer


def test_term_id_is_corpus_independent():
    """The core invariant. If this breaks, sparse retrieval silently rots."""
    a = BM25Tokenizer(); a.fit(["entropy and emergence", "qdrant hybrid search"])
    b = BM25Tokenizer(); b.fit(["aardvark sorts first", "entropy and emergence",
                                "qdrant hybrid search"])
    probe = "entropy qdrant search"
    assert set(a.encode(probe).indices) == set(b.encode(probe).indices)


def test_term_id_is_stable_across_instances():
    assert BM25Tokenizer.term_id("entropy") == BM25Tokenizer.term_id("entropy")
    assert BM25Tokenizer.term_id("entropy") != BM25Tokenizer.term_id("emergence")


def test_term_id_fits_qdrant_sparse_index_range():
    """Qdrant sparse indices must be non-negative and fit u32."""
    for token in ("a", "entropy", "çğıöşü", "x" * 200, "123"):
        tid = BM25Tokenizer.term_id(token)
        assert 0 <= tid < 2 ** 31


def test_fit_does_not_assign_positional_ids():
    """vocab maps to term_id now, not to enumerate() positions."""
    t = BM25Tokenizer(); t.fit(["alpha beta gamma"])
    assert set(t.vocab.values()) != set(range(len(t.vocab))), \
        "vocab values look positional — the defect is back"
    for token, value in t.vocab.items():
        assert value == BM25Tokenizer.term_id(token)


def test_encode_before_fit_returns_empty():
    v = BM25Tokenizer().encode("anything at all")
    assert list(v.indices) == [] and list(v.values) == []


def test_save_writes_format_version(tmp_path):
    t = BM25Tokenizer(); t.fit(["one two three"])
    p = tmp_path / "state.json"; t.save(str(p))
    assert json.loads(p.read_text())["format_version"] == BM25Tokenizer.FORMAT_VERSION


def test_load_rejects_legacy_state(tmp_path):
    """A pre-fix state file must fail loudly, not load and reproduce the bug."""
    p = tmp_path / "legacy.json"
    p.write_text(json.dumps({"k1": 1.2, "b": 0.75, "vocab": {"a": 0},
                             "idf": {"a": 1.0}, "avgdl": 3.0, "n_docs": 1}))
    with pytest.raises(ValueError, match="legacy"):
        BM25Tokenizer().load(str(p))


def test_roundtrip_preserves_ids(tmp_path):
    t = BM25Tokenizer(); t.fit(["entropy and emergence in systems"])
    p = tmp_path / "s.json"; t.save(str(p))
    loaded = BM25Tokenizer(); loaded.load(str(p))
    q = "entropy systems"
    assert list(t.encode(q).indices) == list(loaded.encode(q).indices)
