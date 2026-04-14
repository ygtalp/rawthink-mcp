"""Embedding and Qdrant indexer for RAWThink vault content.

Embeds thought chunks via Ollama (BGE-M3) and stores them in Qdrant
with both dense and BM25 sparse vectors for hybrid retrieval.
"""
from __future__ import annotations

import hashlib
import glob
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import ollama
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    PointStruct,
    SparseVector as QdrantSparseVector,
    HnswConfigDiff,
    Filter,
    FieldCondition,
    MatchValue,
    PayloadSchemaType,
    Prefetch,
    FusionQuery,
    Fusion,
)

from . import config
from .chunker import MarkdownChunker, ThoughtChunk
from .sparse import BM25Tokenizer


class EmbeddingError(RuntimeError):
    """Raised when Ollama embedding fails."""


class Indexer:
    """Embeds vault content and indexes into Qdrant for hybrid search."""

    def __init__(self, qdrant_url: str | None = None, qdrant_path: str | None = None) -> None:
        self._qdrant_url = qdrant_url or config.QDRANT_URL
        self._qdrant_path = qdrant_path or config.QDRANT_PATH
        self._client: QdrantClient | None = None
        self._chunker = MarkdownChunker()
        self._bm25 = BM25Tokenizer()
        self._collection = config.COLLECTION_NAME
        self._bm25_path = os.path.join(os.path.dirname(__file__), ".bm25_state.json")
        # Embedding cache for Ollama fallback (hash -> vector)
        self._embedding_cache: dict[str, list[float]] = {}
        self._cache_keys: list[str] = []  # insertion order for LRU eviction

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create Qdrant client and ensure the collection exists."""
        if self._qdrant_path:
            self._client = QdrantClient(path=self._qdrant_path)
        else:
            self._client = QdrantClient(url=self._qdrant_url)

        if os.path.exists(self._bm25_path):
            self._bm25.load(self._bm25_path)

        collections = [c.name for c in self._client.get_collections().collections]
        if self._collection not in collections:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config={
                    "dense": VectorParams(
                        size=config.VECTOR_DIM,
                        distance=Distance.COSINE,
                    ),
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(),
                },
                hnsw_config=HnswConfigDiff(m=16, ef_construct=128),
            )

            for field_name in ("session_id", "source_type", "tags", "date"):
                self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                )

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def _cache_put(self, key: str, vec: list[float]) -> None:
        """Store embedding in LRU cache, evicting oldest if full."""
        if key in self._embedding_cache:
            return
        if len(self._embedding_cache) >= config.EMBEDDING_CACHE_SIZE:
            oldest = self._cache_keys.pop(0)
            self._embedding_cache.pop(oldest, None)
        self._embedding_cache[key] = vec
        self._cache_keys.append(key)

    def _embed(self, text: str) -> list[float]:
        """Embed a single text via Ollama with cache."""
        cache_key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        try:
            resp = ollama.embed(
                model=config.OLLAMA_MODEL,
                input=config.EMBED_INSTRUCTION + text,
            )
            vec = resp.embeddings[0][: config.VECTOR_DIM]
            self._cache_put(cache_key, vec)
            return vec
        except (httpx.ConnectError, ollama.ResponseError):
            # Try cache
            if cache_key in self._embedding_cache:
                return self._embedding_cache[cache_key]
            raise EmbeddingError(
                "Ollama unavailable and embedding not in cache"
            )

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts via Ollama with caching."""
        prefixed = [config.EMBED_INSTRUCTION + t for t in texts]
        try:
            resp = ollama.embed(model=config.OLLAMA_MODEL, input=prefixed)
            vecs = [v[: config.VECTOR_DIM] for v in resp.embeddings]
            # Cache all results
            for text, vec in zip(texts, vecs):
                cache_key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
                self._cache_put(cache_key, vec)
            return vecs
        except (httpx.ConnectError, ollama.ResponseError) as exc:
            raise EmbeddingError(
                f"Ollama unavailable for batch embedding: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Point ID
    # ------------------------------------------------------------------

    @staticmethod
    def _point_id(session_id: str, chunk_index: int) -> str:
        """Deterministic point ID from session_id and chunk_index."""
        key = f"{session_id}:{chunk_index}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_vault(self) -> int:
        """Index all sessions and qnotes in the vault. Returns total chunk count."""
        vault = Path(config.VAULT_PATH).resolve()

        # Collect all markdown files
        patterns = [
            str(vault / config.SESSIONS_GLOB),
            str(vault / config.QNOTES_GLOB),
        ]

        md_files: list[str] = []
        for pattern in patterns:
            md_files.extend(glob.glob(pattern, recursive=True))

        if not md_files:
            print("No markdown files found in vault.")
            return 0

        # First pass: chunk everything
        all_chunks: list[ThoughtChunk] = []
        for fpath in md_files:
            text = Path(fpath).read_text(encoding="utf-8")
            rel_path = str(Path(fpath).relative_to(vault)).replace("\\", "/")
            chunks = self._chunker.chunk_file(text, rel_path)
            all_chunks.extend(chunks)
            print(f"  Chunked: {rel_path} -> {len(chunks)} chunks")

        if not all_chunks:
            print("No chunks produced.")
            return 0

        # Fit BM25 on entire corpus
        self._bm25.fit([c.chunk_text for c in all_chunks])
        self._bm25.save(self._bm25_path)
        print(f"  BM25 fitted on {self._bm25.n_docs} chunks, vocab size: {len(self._bm25.vocab)}")

        # Upsert in batches of 32, skipping unchanged
        total = 0
        for i in range(0, len(all_chunks), 32):
            batch = all_chunks[i : i + 32]
            total += self._upsert_chunks(batch, skip_unchanged=True)

        print(f"  Total indexed: {total} chunks")
        return total

    def index_session(self, session_id: str) -> int:
        """Index a single session file. Returns chunk count."""
        vault = Path(config.VAULT_PATH).resolve()
        pattern = str(vault / "sessions" / f"*{session_id}*.md")
        files = glob.glob(pattern)

        if not files:
            return 0

        total = 0
        for fpath in files:
            text = Path(fpath).read_text(encoding="utf-8")
            rel_path = str(Path(fpath).relative_to(vault)).replace("\\", "/")
            chunks = self._chunker.chunk_file(text, rel_path)
            if chunks:
                total += self._upsert_chunks(chunks)

        return total

    def _get_existing_hashes(self, point_ids: list[str]) -> dict[str, str]:
        """Fetch content_hash for existing points by ID."""
        if not point_ids:
            return {}
        try:
            points = self._client.retrieve(
                collection_name=self._collection,
                ids=point_ids,
                with_payload=["content_hash"],
            )
            return {
                p.id: p.payload.get("content_hash", "")
                for p in points
            }
        except Exception:
            return {}

    def _upsert_chunks(
        self, chunks: list[ThoughtChunk], skip_unchanged: bool = False
    ) -> int:
        """Embed chunks and upsert into Qdrant. Returns count."""
        if skip_unchanged:
            id_to_chunk = {}
            for chunk in chunks:
                pid = self._point_id(chunk.session_id, chunk.chunk_index)
                id_to_chunk[pid] = chunk

            existing = self._get_existing_hashes(list(id_to_chunk.keys()))
            changed = []
            skipped = 0
            for pid, chunk in id_to_chunk.items():
                if existing.get(pid) == chunk.content_hash and chunk.content_hash:
                    skipped += 1
                else:
                    changed.append(chunk)

            if skipped:
                print(f"  Skipped {skipped} unchanged chunks")
            if not changed:
                return 0
            chunks = changed

        texts = [c.chunk_text for c in chunks]
        dense_vectors = self._embed_batch(texts)

        points = []
        for chunk, dense_vec in zip(chunks, dense_vectors):
            sparse = self._bm25.encode(chunk.chunk_text)
            point_id = self._point_id(chunk.session_id, chunk.chunk_index)

            payload = chunk.to_payload()
            payload["indexed_at"] = datetime.now(timezone.utc).isoformat()

            point = PointStruct(
                id=point_id,
                vector={
                    "dense": dense_vec,
                    "sparse": QdrantSparseVector(
                        indices=sparse.indices,
                        values=sparse.values,
                    ),
                },
                payload=payload,
            )
            points.append(point)

        self._client.upsert(collection_name=self._collection, points=points)
        return len(points)

    def index_chunk(self, chunk: ThoughtChunk) -> int:
        """Index a single chunk into Qdrant. Returns count (1 on success)."""
        return self._upsert_chunks([chunk])

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 10,
        filters: Filter | None = None,
    ) -> list[dict]:
        """Hybrid search: sparse BM25 + dense embedding fused with RRF.

        Falls back to sparse-only search if Ollama embedding fails.
        """
        sparse_vec = self._bm25.encode(query)
        sparse_only = False

        try:
            dense_vec = self._embed(query)
        except EmbeddingError:
            dense_vec = None
            sparse_only = True

        if sparse_only or dense_vec is None:
            # Sparse-only fallback (BM25 only)
            results = self._client.query_points(
                collection_name=self._collection,
                query=QdrantSparseVector(
                    indices=sparse_vec.indices,
                    values=sparse_vec.values,
                ),
                using="sparse",
                limit=limit,
                query_filter=filters,
            )
        else:
            # Full hybrid search
            results = self._client.query_points(
                collection_name=self._collection,
                prefetch=[
                    Prefetch(
                        query=QdrantSparseVector(
                            indices=sparse_vec.indices,
                            values=sparse_vec.values,
                        ),
                        using="sparse",
                        limit=20,
                    ),
                    Prefetch(
                        query=dense_vec,
                        using="dense",
                        limit=20,
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=limit,
                query_filter=filters,
            )

        hits = []
        for point in results.points:
            hit = dict(point.payload)
            hit["score"] = point.score
            hit["id"] = point.id
            if sparse_only:
                hit["_warning"] = "sparse-only search (Ollama unavailable)"
            hits.append(hit)
        return hits

    # ------------------------------------------------------------------
    # Session retrieval
    # ------------------------------------------------------------------

    def get_session_chunks(self, session_id: str) -> list[dict]:
        """Retrieve all indexed chunks for a session, sorted by chunk_index."""
        results, _ = self._client.scroll(
            collection_name=self._collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="session_id", match=MatchValue(value=session_id))]
            ),
            limit=1000,
        )
        chunks = []
        for point in results:
            chunk = dict(point.payload)
            chunk["id"] = point.id
            chunks.append(chunk)

        chunks.sort(key=lambda c: c.get("chunk_index", 0))
        return chunks

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_session(self, session_id: str) -> None:
        """Delete all chunks for a session."""
        self._client.delete(
            collection_name=self._collection,
            points_selector=Filter(
                must=[FieldCondition(key="session_id", match=MatchValue(value=session_id))]
            ),
        )


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

if __name__ == "__main__":
    if "--index-vault" in sys.argv:
        idx = Indexer()
        idx.initialize()
        print("=== RAWThink Vault Indexer ===\n")
        count = idx.index_vault()
        print(f"\nDone. Total: {count} chunks indexed.")
    else:
        print("Usage: python indexer.py --index-vault")
