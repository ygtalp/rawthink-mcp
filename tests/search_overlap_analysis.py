"""Search Overlap Analysis: graph search_nodes vs Qdrant search_thoughts.

Runs the same queries against both search systems and compares results.

Purpose: Understand whether the two systems return complementary or
redundant information for the same queries.

Architecture recap:
  - search_nodes (graph.py) → searches memory.jsonl entities via inverted
    index + Turkish normalization. Returns entity names, types, observations.
  - search_thoughts (indexer.py) → searches sessions/*.md + qnotes/*.md via
    Qdrant hybrid (dense BGE-M3 + BM25 sparse, RRF fusion). Returns chunk
    text, session IDs, scores.

These search DIFFERENT data stores. The question is: for the same query,
do they surface the same conceptual information, or different?

Usage:
  cd rawthink-mcp
  python -m tests.search_overlap_analysis

Requires: Qdrant running (localhost:6333), Ollama running (BGE-M3), vault populated.
"""
from __future__ import annotations

import json
import sys
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

# Add parent to path so we can import rawthink_mcp
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rawthink_mcp.graph import KnowledgeGraph, normalize_turkish
from rawthink_mcp.indexer import Indexer, EmbeddingError


# ---------------------------------------------------------------------------
# Test queries — designed to cover different retrieval scenarios
# ---------------------------------------------------------------------------

QUERIES = [
    # Philosophical concepts — should hit both graph entities AND session text
    "bilinc kurallari",
    "simulasyon hipotezi",
    "entropi garbage collector",
    # Technical architecture — graph has entities, sessions have discussion
    "rawthink mimari",
    "qdrant hybrid search",
    # Personal decisions — stored as graph entities AND discussed in sessions
    "NL relocation",
    "vitamin D eksikligi",
    # Cross-cutting themes
    "strange loop Hofstadter",
    "activation decay",
    # Broad / ambiguous
    "ozgur irade determinizm",
]


@dataclass
class SearchResult:
    """Unified result from either search system."""
    source: str           # "graph" or "qdrant"
    identifier: str       # entity name or session_id
    text_preview: str     # first 200 chars of relevant text
    score: float = 0.0


@dataclass
class QueryAnalysis:
    """Analysis of a single query across both systems."""
    query: str
    graph_results: list[SearchResult] = field(default_factory=list)
    qdrant_results: list[SearchResult] = field(default_factory=list)
    graph_time_ms: float = 0.0
    qdrant_time_ms: float = 0.0

    @property
    def graph_identifiers(self) -> set[str]:
        return {r.identifier for r in self.graph_results}

    @property
    def qdrant_sessions(self) -> set[str]:
        return {r.identifier for r in self.qdrant_results}

    @property
    def conceptual_overlap(self) -> list[str]:
        """Find entity names mentioned in Qdrant results text."""
        overlaps = []
        qdrant_text = " ".join(
            normalize_turkish(r.text_preview) for r in self.qdrant_results
        )
        for gr in self.graph_results:
            # Check if entity name (normalized) appears in Qdrant text
            name_norm = normalize_turkish(gr.identifier)
            # Also check without hyphens (entity names are kebab-case)
            name_words = name_norm.replace("-", " ")
            if name_norm in qdrant_text or name_words in qdrant_text:
                overlaps.append(gr.identifier)
        return overlaps


def run_graph_search(kg: KnowledgeGraph, query: str, limit: int = 10) -> tuple[list[SearchResult], float]:
    """Run graph search_nodes and return results + time."""
    start = time.perf_counter()
    result = kg.search_nodes(query)
    elapsed = (time.perf_counter() - start) * 1000

    results = []
    for entity in result.get("entities", [])[:limit]:
        obs_texts = []
        for obs in entity.get("observations", [])[:3]:
            if isinstance(obs, dict):
                obs_texts.append(obs.get("text", ""))
            else:
                obs_texts.append(str(obs))
        preview = " | ".join(obs_texts)[:200]
        results.append(SearchResult(
            source="graph",
            identifier=entity.get("name", ""),
            text_preview=preview,
        ))
    return results, elapsed


def run_qdrant_search(indexer: Indexer, query: str, limit: int = 10) -> tuple[list[SearchResult], float]:
    """Run Qdrant hybrid search and return results + time."""
    start = time.perf_counter()
    try:
        hits = indexer.search(query=query, limit=limit)
    except EmbeddingError as exc:
        print(f"  WARNING: Embedding error: {exc}")
        return [], (time.perf_counter() - start) * 1000

    elapsed = (time.perf_counter() - start) * 1000

    results = []
    for hit in hits:
        results.append(SearchResult(
            source="qdrant",
            identifier=hit.get("session_id", ""),
            text_preview=hit.get("chunk_text", "")[:200],
            score=hit.get("score", 0),
        ))
    return results, elapsed


def analyze_query(kg: KnowledgeGraph, indexer: Indexer, query: str) -> QueryAnalysis:
    """Run both searches and analyze overlap."""
    analysis = QueryAnalysis(query=query)

    analysis.graph_results, analysis.graph_time_ms = run_graph_search(kg, query)
    analysis.qdrant_results, analysis.qdrant_time_ms = run_qdrant_search(indexer, query)

    return analysis


def print_report(analyses: list[QueryAnalysis]) -> None:
    """Print the full overlap analysis report."""
    print("=" * 80)
    print("SEARCH OVERLAP ANALYSIS REPORT")
    print("graph search_nodes vs Qdrant search_thoughts")
    print("=" * 80)

    total_graph = 0
    total_qdrant = 0
    total_overlap = 0
    total_graph_only = 0
    total_qdrant_only = 0

    for i, a in enumerate(analyses, 1):
        print(f"\n{'─' * 70}")
        print(f"Query {i}: \"{a.query}\"")
        print(f"{'─' * 70}")

        g_count = len(a.graph_results)
        q_count = len(a.qdrant_results)
        overlap = a.conceptual_overlap

        print(f"  Graph:  {g_count} entities ({a.graph_time_ms:.1f}ms)")
        for r in a.graph_results[:5]:
            print(f"    - {r.identifier}: {r.text_preview[:80]}...")

        print(f"  Qdrant: {q_count} chunks ({a.qdrant_time_ms:.1f}ms)")
        seen = set()
        for r in a.qdrant_results[:5]:
            if r.identifier not in seen:
                print(f"    - [{r.identifier}] score={r.score:.4f}: {r.text_preview[:60]}...")
                seen.add(r.identifier)

        print(f"  Conceptual overlap: {len(overlap)} entities found in Qdrant text")
        if overlap:
            print(f"    Overlapping: {', '.join(overlap)}")

        total_graph += g_count
        total_qdrant += q_count
        total_overlap += len(overlap)
        total_graph_only += g_count - len(overlap)
        total_qdrant_only += q_count  # All Qdrant results are from a different store

    # Summary
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    print(f"  Total queries:             {len(analyses)}")
    print(f"  Total graph results:       {total_graph}")
    print(f"  Total Qdrant results:      {total_qdrant}")
    print(f"  Conceptual overlaps:       {total_overlap}")
    print(f"  Graph-only (unique info):  {total_graph_only}")
    print(f"  Qdrant-only (unique info): {total_qdrant_only}")

    if total_graph > 0:
        overlap_rate = total_overlap / total_graph * 100
        print(f"  Overlap rate:              {overlap_rate:.1f}% of graph entities appear in Qdrant text")
    print()

    # Complementarity assessment
    print("ASSESSMENT:")
    if total_graph > 0 and total_qdrant > 0:
        if total_overlap / total_graph < 0.3:
            print("  Systems are HIGHLY COMPLEMENTARY — they surface different information.")
            print("  Recommendation: Keep both. Consider unified search endpoint that queries both.")
        elif total_overlap / total_graph < 0.6:
            print("  Systems are MODERATELY COMPLEMENTARY — some overlap but distinct value.")
            print("  Recommendation: Keep both. Consider indexing entities into Qdrant for unified ranking.")
        else:
            print("  Systems have HIGH OVERLAP — consider merging into single search.")
            print("  Recommendation: Index entity text into Qdrant, deprecate separate graph search.")
    elif total_graph == 0:
        print("  Graph returned no results. Graph may be empty or queries don't match entity content.")
    elif total_qdrant == 0:
        print("  Qdrant returned no results. Qdrant may not be indexed or Ollama is down.")

    # Latency comparison
    avg_graph = sum(a.graph_time_ms for a in analyses) / len(analyses)
    avg_qdrant = sum(a.qdrant_time_ms for a in analyses) / len(analyses)
    print(f"\n  Avg latency — Graph: {avg_graph:.1f}ms, Qdrant: {avg_qdrant:.1f}ms")

    # Recommendation for entity indexing
    print(f"\n{'─' * 70}")
    print("ENTITY → QDRANT INDEXING QUESTION:")
    print("  Currently entities are NOT in Qdrant. If indexed, search_thoughts")
    print("  would also find entity content. This would simplify the API but:")
    print("  - Adds maintenance cost (two indexes to keep in sync)")
    print("  - Graph search is instant (inverted index) vs Qdrant (embedding + network)")
    print("  - Graph search returns structured data (entity type, relations)")
    print("  - Qdrant returns unstructured text chunks")
    print("  Verdict: Keep both unless overlap rate > 60%.")
    print(f"{'─' * 70}")


def main():
    print("Initializing search systems...")

    # Initialize graph
    kg = KnowledgeGraph()
    items = kg._read_all()
    entities = [i for i in items if i.get("type") == "entity"]
    relations = [i for i in items if i.get("type") == "relation"]
    print(f"  Graph: {len(entities)} entities, {len(relations)} relations")

    # Initialize Qdrant indexer
    indexer = Indexer()
    try:
        indexer.initialize()
        # Quick check: how many points in collection?
        info = indexer._client.get_collection(indexer._collection)
        print(f"  Qdrant: {info.points_count} points in '{indexer._collection}'")
    except Exception as exc:
        print(f"  Qdrant init failed: {exc}")
        print("  Make sure Qdrant is running (docker) and vault is indexed.")
        sys.exit(1)

    print(f"\nRunning {len(QUERIES)} queries against both systems...\n")

    analyses = []
    for query in QUERIES:
        analysis = analyze_query(kg, indexer, query)
        analyses.append(analysis)

    print_report(analyses)


if __name__ == "__main__":
    main()
