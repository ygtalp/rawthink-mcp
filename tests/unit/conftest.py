"""Shared fixtures.

Three tiers, so most of the suite runs anywhere and the expensive parts stay
opt-in:

  T1  no infrastructure          pure logic — graph, sparse, chunker, config
  T2  in-memory Qdrant + stub    indexing plumbing, dedup, pagination
  T3  real Qdrant + real Ollama  retrieval quality, marked and skipped by default

The split works because the dependencies sit at the edges: seven of nine
modules import neither qdrant nor ollama. Only `indexer` and `server` do, and
`indexer` now takes an injectable embedder, which is what makes T2 possible
without a model server.

Run T3 explicitly:

    RAWTHINK_TEST_QDRANT=http://localhost:6333 \\
    RAWTHINK_TEST_OLLAMA=http://localhost:11434 \\
    pytest -m integration
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path

import pytest

VECTOR_DIM = 1024


# ---------------------------------------------------------------------------
# T1 — no infrastructure
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """An empty vault with the directory layout the code expects."""
    for sub in ("sessions", "qnotes", "outputs", "archive/raw"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory.jsonl").write_text("", encoding="utf-8")
    return tmp_path


@pytest.fixture
def kg(vault: Path):
    """A KnowledgeGraph on a throwaway file."""
    from rawthink_mcp.graph import KnowledgeGraph
    return KnowledgeGraph(str(vault / "memory.jsonl"))


@pytest.fixture
def session_file(vault: Path):
    """Write a session markdown file into the vault."""
    def _write(session_id: str, title: str, body: str, tags: list[str] | None = None) -> Path:
        tag_yaml = ", ".join(f'"{t}"' for t in (tags or []))
        path = vault / "sessions" / f"{session_id}_{title.lower().replace(' ', '-')}.md"
        path.write_text(
            f"---\nid: \"{session_id}\"\ntitle: \"{title}\"\n"
            f"date: 2026-01-01\ntags: [{tag_yaml}]\n---\n\n{body}\n",
            encoding="utf-8",
        )
        return path
    return _write


# ---------------------------------------------------------------------------
# T2 — in-memory Qdrant, deterministic stub embedder
# ---------------------------------------------------------------------------

def _stub_vector(text: str, dim: int = VECTOR_DIM) -> list[float]:
    """Deterministic pseudo-embedding.

    Same text always yields the same vector, and different texts yield
    different ones — which is all the plumbing tests need. It carries no
    semantic similarity, so it can verify that a chunk was indexed, deduped or
    paginated, but never that retrieval is *good*. That question belongs to
    T3, against the real model.
    """
    out: list[float] = []
    counter = 0
    while len(out) < dim:
        digest = hashlib.blake2b(
            text.encode("utf-8") + struct.pack("<I", counter), digest_size=64
        ).digest()
        out.extend((b - 127.5) / 127.5 for b in digest)
        counter += 1
    vec = out[:dim]
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


@pytest.fixture
def stub_embedder():
    """An embedder that needs no model server."""
    calls: list[list[str]] = []

    def _embed(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return [_stub_vector(t) for t in texts]

    _embed.calls = calls  # type: ignore[attr-defined]
    return _embed


@pytest.fixture
def broken_embedder():
    """Returns fewer vectors than texts — the silent-truncation case."""
    def _embed(texts: list[str]) -> list[list[float]]:
        return [_stub_vector(t) for t in texts[:-1]] if len(texts) > 1 else []
    return _embed


@pytest.fixture
def indexer(vault: Path, tmp_path: Path, stub_embedder, monkeypatch):
    """An Indexer backed by in-memory Qdrant and the stub embedder."""
    from rawthink_mcp import config
    from rawthink_mcp.indexer import Indexer

    monkeypatch.setattr(config, "VAULT_PATH", str(vault))
    monkeypatch.setattr(config, "MEMORY_FILE", str(vault / "memory.jsonl"))

    # bm25_state_path is explicit. Without it the Indexer falls back to
    # config.BM25_STATE_FILE, which resolves next to the package — so a test
    # run writes a BM25 state file into the repo's own vault directory. That
    # actually happened, and it is what the "path is a constructor argument"
    # rule exists to prevent.
    idx = Indexer(qdrant_path=":memory:", embedder=stub_embedder,
                  bm25_state_path=str(tmp_path / "bm25_state.json"))
    idx.initialize()
    yield idx
    client = getattr(idx, "_client", None)
    if client is not None:
        try:
            client.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# T3 — real services, opt-in
# ---------------------------------------------------------------------------

def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value or None


@pytest.fixture(scope="session")
def real_qdrant_url() -> str:
    url = _env("RAWTHINK_TEST_QDRANT")
    if not url:
        pytest.skip("set RAWTHINK_TEST_QDRANT to run integration tests")
    return url


@pytest.fixture(scope="session")
def real_ollama_url() -> str:
    url = _env("RAWTHINK_TEST_OLLAMA")
    if not url:
        pytest.skip("set RAWTHINK_TEST_OLLAMA to run integration tests")
    return url


@pytest.fixture(scope="session")
def real_vault() -> Path:
    """A vault with real content — for measurement, never for mutation."""
    raw = _env("RAWTHINK_TEST_VAULT")
    if not raw:
        pytest.skip("set RAWTHINK_TEST_VAULT to run measurement tests")
    path = Path(raw).expanduser()
    if not (path / "memory.jsonl").exists():
        pytest.skip(f"no memory.jsonl under {path}")
    return path


# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: needs a real Qdrant and/or Ollama (opt-in)")
    config.addinivalue_line(
        "markers", "measurement: reports numbers rather than asserting; needs a real vault")
    config.addinivalue_line(
        "markers", "slow: takes more than a second")


@pytest.fixture
def read_jsonl():
    def _read(path: Path) -> list[dict]:
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return _read
