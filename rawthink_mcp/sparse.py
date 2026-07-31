"""BM25 sparse vector tokenizer for RAWThink vault content.

Tokenizes Turkish+English markdown text and produces BM25-weighted sparse
vectors suitable for Qdrant hybrid search alongside dense embeddings.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SparseVector:
    """Sparse vector representation with matching index/value pairs."""

    indices: list[int] = field(default_factory=list)
    values: list[float] = field(default_factory=list)


# Regex for splitting CamelCase / PascalCase boundaries
_CAMEL_SPLIT = re.compile(
    r"(?<=[a-z])(?=[A-Z])"
    r"|(?<=[A-Z])(?=[A-Z][a-z])"
)

# Turkish + English stop words (common, low-signal words)
_STOP_WORDS = frozenset({
    # Turkish
    "ve", "bir", "bu", "ile", "da", "de", "ama", "icin", "için",
    "var", "yok", "mi", "mu", "mı", "gibi", "ki", "ne", "ben",
    "sen", "biz", "siz", "onlar", "ise", "ya", "hem", "daha",
    "cok", "çok", "her", "sey", "şey", "olan", "olarak", "sonra",
    "kadar", "zaman", "bile", "sadece", "eger", "eğer", "ama",
    "fakat", "ancak", "veya", "ya da",
    # English
    "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "shall",
    "and", "or", "but", "not", "no", "nor",
    "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "as", "into", "about", "that", "this", "it", "its",
    "an", "if", "so", "than", "too", "very",
})


class BM25Tokenizer:
    """Tokenizes text and encodes as BM25-weighted sparse vectors.

    Usage:
        tok = BM25Tokenizer()
        tok.fit(corpus_texts)
        vec = tok.encode("what were the rules of consciousness?")
    """

    FORMAT_VERSION = 2

    def __init__(self, k1: float = 1.2, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.vocab: dict[str, int] = {}
        self.idf: dict[str, float] = {}
        self.avgdl: float = 0.0
        self.n_docs: int = 0

    # ------------------------------------------------------------------
    # Tokenization
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Term IDs
    # ------------------------------------------------------------------

    @staticmethod
    def term_id(token: str) -> int:
        """Stable, corpus-independent ID for a token.

        Positional IDs — `enumerate(sorted(vocab))` — were the defect this
        replaces. One new token sorting early shifted every ID after it, so
        vectors written before a reindex no longer matched queries encoded
        after it. Nothing failed; the sparse side simply stopped matching, and
        RRF hid it because the dense side still worked.

        A hash of the token depends on nothing but the token, so an ID assigned
        today is the same ID next year on a different corpus.

        31 bits: Qdrant sparse indices must be non-negative and fit u32.
        """
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
        return int.from_bytes(digest, "big") & 0x7FFFFFFF

    @staticmethod
    def tokenize(text: str) -> list[str]:
        """Tokenize text into searchable tokens.

        Steps:
          1. Split by whitespace and punctuation
          2. Lowercase
          3. CamelCase split for technical terms
          4. Remove stop words and short tokens (< 2 chars)
        """
        # Split on whitespace, punctuation, markdown syntax
        raw_tokens = re.split(r"[\s.,;:!?\"\'`#\-\—\(\)\[\]{}<>/*_=|>~^]+", text)

        tokens: list[str] = []
        for raw in raw_tokens:
            if not raw:
                continue
            lower_full = raw.lower()

            # Skip stop words
            if lower_full in _STOP_WORDS:
                continue

            # CamelCase split
            parts = _CAMEL_SPLIT.split(raw)
            lower_parts = [p.lower() for p in parts if len(p) >= 2]

            # Always include the full token (lowered)
            if len(lower_full) >= 2:
                tokens.append(lower_full)

            # Add sub-parts only when actually split
            if len(lower_parts) > 1:
                tokens.extend(p for p in lower_parts if p not in _STOP_WORDS)

        return tokens

    # ------------------------------------------------------------------
    # Fit (build vocab + IDF)
    # ------------------------------------------------------------------

    def fit(self, documents: list[str]) -> None:
        """Build vocabulary and IDF values from a corpus of documents."""
        self.n_docs = len(documents)
        if self.n_docs == 0:
            return

        df: dict[str, int] = {}
        total_length = 0

        for doc in documents:
            doc_tokens = self.tokenize(doc)
            total_length += len(doc_tokens)
            unique_tokens = set(doc_tokens)
            for token in unique_tokens:
                df[token] = df.get(token, 0) + 1

        self.avgdl = total_length / self.n_docs

        # vocab is no longer the source of IDs — it is a "have we seen this
        # token" set plus a place to hang statistics. IDs come from term_id().
        self.vocab = {token: self.term_id(token) for token in df}

        self.idf = {}
        for token, freq in df.items():
            numerator = self.n_docs - freq + 0.5
            denominator = freq + 0.5
            self.idf[token] = math.log(numerator / denominator + 1.0)

    # ------------------------------------------------------------------
    # Encode
    # ------------------------------------------------------------------

    def encode(self, text: str) -> SparseVector:
        """Produce a BM25-weighted sparse vector for the given text."""
        tokens = self.tokenize(text)
        doc_len = len(tokens)

        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1

        indices: list[int] = []
        values: list[float] = []

        for token, freq in sorted(tf.items()):
            if token not in self.vocab:
                continue
            idf = self.idf.get(token, 0.0)
            numerator = freq * (self.k1 + 1)
            denominator = freq + self.k1 * (
                1 - self.b + self.b * doc_len / max(self.avgdl, 1e-6)
            )
            score = idf * (numerator / denominator)
            if score > 0:
                indices.append(self.term_id(token))
                values.append(round(score, 6))

        return SparseVector(indices=indices, values=values)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Serialize vocab + IDF state to a JSON file."""
        state = {
            # Bumped when the ID scheme changes. A state file written under the
            # old positional scheme is not merely outdated — it is wrong, and
            # loading it silently would reproduce the exact defect this fixes.
            "format_version": self.FORMAT_VERSION,
            "k1": self.k1,
            "b": self.b,
            "vocab": self.vocab,
            "idf": self.idf,
            "avgdl": self.avgdl,
            "n_docs": self.n_docs,
        }
        Path(path).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load(self, path: str) -> None:
        """Deserialize vocab + IDF state from a JSON file."""
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        version = state.get("format_version", 1)
        if version < self.FORMAT_VERSION:
            raise ValueError(
                f"legacy BM25 state (format_version={version}, need "
                f"{self.FORMAT_VERSION}). Term IDs were corpus-dependent in "
                f"that format. Delete {path} and run a full reindex — "
                f"reindex(full=True) — to re-encode every chunk."
            )
        self.k1 = state["k1"]
        self.b = state["b"]
        self.vocab = state["vocab"]
        self.idf = state["idf"]
        self.avgdl = state["avgdl"]
        self.n_docs = state["n_docs"]
