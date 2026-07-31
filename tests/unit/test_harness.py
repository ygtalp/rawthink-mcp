"""Tests that the harness itself works.

If these fail, nothing else in the suite means anything.
"""
import pytest


# --- T1: no infrastructure -------------------------------------------------

def test_vault_fixture_has_layout(vault):
    for sub in ("sessions", "qnotes"):
        assert (vault / sub).is_dir()
    assert (vault / "memory.jsonl").exists()


def test_kg_writes_and_reads(kg):
    kg.record(entities=[{"name": "a", "entityType": "concept", "domain": "software",
                         "observations": ["first"]}])
    found = kg.search_nodes("a")
    assert found["total_matched"] == 1
    assert found["entities"][0]["domain"] == "software"


def test_kg_is_isolated_per_test(kg):
    """Each test gets an empty graph — no leakage from the previous one."""
    assert kg.search_nodes("a")["total_matched"] == 0


# --- T2: in-memory Qdrant + stub embedder ----------------------------------

def test_stub_embedder_is_deterministic(stub_embedder):
    a = stub_embedder(["hello"])[0]
    b = stub_embedder(["hello"])[0]
    c = stub_embedder(["goodbye"])[0]
    assert a == b, "same text must embed identically"
    assert a != c, "different text must embed differently"
    assert len(a) == 1024
    assert abs(sum(v * v for v in a) ** 0.5 - 1.0) < 1e-6, "should be unit length"


def test_indexer_runs_without_ollama_or_docker(indexer, vault, session_file):
    session_file("2026-01-01_001", "Harness", "Some content about caching decisions.")
    count = indexer.index_vault()
    assert count > 0, "chunks should have been indexed"


def test_indexer_search_returns_indexed_content(indexer, vault, session_file):
    session_file("2026-01-01_001", "Caching", "We chose a read-through cache.")
    indexer.index_vault()
    hits = indexer.search(query="read-through cache", limit=5)
    assert hits, "the indexed chunk should come back"
    assert any("cache" in h.get("chunk_text", "").lower() for h in hits)


def test_embedder_seam_is_actually_used(indexer, vault, session_file, stub_embedder):
    session_file("2026-01-01_001", "Seam", "content")
    indexer.index_vault()
    assert stub_embedder.calls, "the injected embedder should have been called"


# --- T3: skipped unless the environment says otherwise ---------------------

@pytest.mark.integration
def test_real_qdrant_reachable(real_qdrant_url):
    from qdrant_client import QdrantClient
    client = QdrantClient(url=real_qdrant_url)
    client.get_collections()


@pytest.mark.measurement
def test_real_vault_present(real_vault):
    assert (real_vault / "memory.jsonl").stat().st_size > 0
