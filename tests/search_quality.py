"""Search Quality Test Framework.

Measures MRR, Precision@K, Recall@K, NDCG@K for both search systems
(graph search_nodes and Qdrant search_thoughts) against ground truth.

Usage:
  cd rawthink-mcp
  RAWTHINK_VAULT=../vault MEMORY_FILE_PATH=../vault/memory.jsonl \
    python -m tests.search_quality [--after-fix]

Requires: Qdrant running (localhost:6333), Ollama running (BGE-M3).
"""
from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rawthink_mcp.graph import KnowledgeGraph, normalize_turkish
from rawthink_mcp.indexer import Indexer, EmbeddingError


# ---------------------------------------------------------------------------
# Ground truth: 20 query-answer pairs from our vault
# ---------------------------------------------------------------------------

@dataclass
class GroundTruth:
    query: str
    expected_entities: list[str]    # entity names that SHOULD appear in graph results
    expected_sessions: list[str]    # session IDs that SHOULD appear in Qdrant results
    category: str = ""              # for grouping in report


GROUND_TRUTH: list[GroundTruth] = [
    # --- Philosophical concepts ---
    GroundTruth(
        query="bilinc kurallari",
        expected_entities=[
            "bilinc-kurali-1-self-reference",
            "bilinc-kurali-2-compression",
            "bilinc-kurali-3-non-duality",
            "bilinc-kurali-4-acik",
        ],
        expected_sessions=["2026-03-16_002"],
        category="philosophy",
    ),
    GroundTruth(
        query="simulasyon hipotezi evren",
        expected_entities=["simulasyon-hipotezi", "evren-genislemesi"],
        expected_sessions=["2026-03-16_001", "2026-03-28_004"],
        category="philosophy",
    ),
    GroundTruth(
        query="entropi garbage collector",
        expected_entities=["entropi"],
        expected_sessions=["2026-03-16_002"],
        category="philosophy",
    ),
    GroundTruth(
        query="ozgur irade determinizm",
        expected_entities=["ozgur-irade"],
        expected_sessions=["2026-03-16_002"],
        category="philosophy",
    ),
    GroundTruth(
        query="strange loop self reference",
        expected_entities=[
            "bilinc-kurali-1-self-reference",
            "spark-self-reference",
            "strange-loop-bilgi-grafi",
        ],
        expected_sessions=["2026-04-10_001", "2026-04-10_002"],
        category="philosophy",
    ),
    GroundTruth(
        query="adem metaforu bilgi agaci",
        expected_entities=["adem-metaforu"],
        expected_sessions=["2026-03-16_002"],
        category="philosophy",
    ),
    # --- Technical decisions ---
    GroundTruth(
        query="rawthink mimari katman",
        expected_entities=["rawthink-mimari", "global-beyin-mimarisi"],
        expected_sessions=["2026-04-09_002", "2026-04-09_003"],
        category="technical",
    ),
    GroundTruth(
        query="qdrant vector search hybrid",
        expected_entities=["rawthink-mimari", "vector-db-arastirmasi-2026"],
        expected_sessions=["2026-04-02_003"],
        category="technical",
    ),
    GroundTruth(
        query="kapat komutu session export",
        expected_entities=["kapat-komutu"],
        expected_sessions=["2026-03-30_002"],
        category="technical",
    ),
    GroundTruth(
        query="activation decay lambda",
        expected_entities=["tier12-implementation", "rawthink-mimari"],
        expected_sessions=["2026-04-12_001"],
        category="technical",
    ),
    GroundTruth(
        query="thinking directives epistemik",
        expected_entities=["thinking-directives", "session-direktifleri"],
        expected_sessions=["2026-04-12_003"],
        category="technical",
    ),
    # --- Personal ---
    GroundTruth(
        query="NL relocation Hollanda",
        expected_entities=["nl-relocation", "yigit-profil"],
        expected_sessions=["2026-03-28_004"],
        category="personal",
    ),
    GroundTruth(
        query="vitamin D eksikligi kan tahlili",
        expected_entities=[
            "vitamin-d-eksikligi",
            "yigit-saglik-tahlil-2026-04",
        ],
        expected_sessions=["2026-04-03_002"],
        category="personal",
    ),
    GroundTruth(
        query="anksiyete pattern kosullu varolus",
        expected_entities=["anksiyete-pattern", "kosullu-varolus-pattern"],
        expected_sessions=["2026-04-02_001"],
        category="personal",
    ),
    # --- DMT & neuroscience ---
    GroundTruth(
        query="DMT molekul default mode network",
        expected_entities=["dmt-molekulu", "dmt-default-mode-network"],
        expected_sessions=["2026-04-03_001"],
        category="dmt",
    ),
    GroundTruth(
        query="uzerlik MAO inhibitor ayahuasca",
        expected_entities=["uzerlik-mao-inhibitor-paradoksu"],
        expected_sessions=["2026-04-03_001"],
        category="dmt",
    ),
    # --- Project management ---
    GroundTruth(
        query="codebase audit results",
        expected_entities=[
            "codebase-audit-2026-04-01",
        ],
        expected_sessions=["2026-04-01_002"],
        category="project",
    ),
    # --- Edge cases ---
    GroundTruth(
        query="D vitamini",
        expected_entities=["vitamin-d-eksikligi"],
        expected_sessions=["2026-04-03_002"],
        category="edge-case",
    ),
    GroundTruth(
        query="Karpathy LLM wiki convergence",
        expected_entities=[
            "karpathy-yakinlasma",
            "personal-neural-graphs-article",
        ],
        expected_sessions=["2026-04-10_001", "2026-04-10_002"],
        category="edge-case",
    ),
]


# ---------------------------------------------------------------------------
# IR Metrics
# ---------------------------------------------------------------------------

def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    """Mean Reciprocal Rank: 1/rank of first relevant result."""
    for i, item in enumerate(retrieved, 1):
        if item in relevant:
            return 1.0 / i
    return 0.0


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Precision@K: fraction of top-K results that are relevant."""
    top_k = retrieved[:k]
    if not top_k:
        return 0.0
    return len(set(top_k) & relevant) / len(top_k)


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Recall@K: fraction of relevant items found in top-K."""
    if not relevant:
        return 1.0  # nothing to find = perfect recall
    top_k = set(retrieved[:k])
    return len(top_k & relevant) / len(relevant)


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """NDCG@K: normalized discounted cumulative gain.
    Binary relevance: 1 if in relevant set, 0 otherwise."""
    top_k = retrieved[:k]

    # DCG
    dcg = 0.0
    for i, item in enumerate(top_k):
        rel = 1.0 if item in relevant else 0.0
        dcg += rel / math.log2(i + 2)  # i+2 because log2(1)=0

    # Ideal DCG: all relevant items first
    ideal_count = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_count))

    if idcg == 0:
        return 0.0
    return dcg / idcg


# ---------------------------------------------------------------------------
# Result extraction
# ---------------------------------------------------------------------------

def extract_graph_entities(kg: KnowledgeGraph, query: str, limit: int = 10) -> tuple[list[str], float]:
    """Run graph search and return ordered entity names + latency."""
    start = time.perf_counter()
    result = kg.search_nodes(query)
    elapsed = (time.perf_counter() - start) * 1000
    names = [e["name"] for e in result.get("entities", [])[:limit]]
    return names, elapsed


def extract_qdrant_sessions(indexer: Indexer, query: str, limit: int = 10) -> tuple[list[str], float]:
    """Run Qdrant search and return ordered session IDs (deduplicated) + latency."""
    start = time.perf_counter()
    try:
        hits = indexer.search(query=query, limit=limit)
    except EmbeddingError:
        return [], (time.perf_counter() - start) * 1000
    elapsed = (time.perf_counter() - start) * 1000

    seen = set()
    sessions = []
    for hit in hits:
        sid = hit.get("session_id", "")
        if sid and sid not in seen:
            seen.add(sid)
            sessions.append(sid)
    return sessions, elapsed


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

@dataclass
class QueryMetrics:
    query: str
    category: str

    # Graph metrics
    graph_mrr: float = 0.0
    graph_p5: float = 0.0
    graph_p10: float = 0.0
    graph_r5: float = 0.0
    graph_r10: float = 0.0
    graph_ndcg5: float = 0.0
    graph_ndcg10: float = 0.0
    graph_time_ms: float = 0.0
    graph_retrieved: list[str] = field(default_factory=list)

    # Qdrant metrics
    qdrant_mrr: float = 0.0
    qdrant_p5: float = 0.0
    qdrant_p10: float = 0.0
    qdrant_r5: float = 0.0
    qdrant_r10: float = 0.0
    qdrant_ndcg5: float = 0.0
    qdrant_ndcg10: float = 0.0
    qdrant_time_ms: float = 0.0
    qdrant_retrieved: list[str] = field(default_factory=list)


def evaluate_query(
    kg: KnowledgeGraph,
    indexer: Indexer,
    gt: GroundTruth,
) -> QueryMetrics:
    """Evaluate a single query against both systems."""
    m = QueryMetrics(query=gt.query, category=gt.category)

    # Graph evaluation
    entities, g_time = extract_graph_entities(kg, gt.query)
    m.graph_retrieved = entities
    m.graph_time_ms = g_time
    relevant_entities = set(gt.expected_entities)

    m.graph_mrr = reciprocal_rank(entities, relevant_entities)
    m.graph_p5 = precision_at_k(entities, relevant_entities, 5)
    m.graph_p10 = precision_at_k(entities, relevant_entities, 10)
    m.graph_r5 = recall_at_k(entities, relevant_entities, 5)
    m.graph_r10 = recall_at_k(entities, relevant_entities, 10)
    m.graph_ndcg5 = ndcg_at_k(entities, relevant_entities, 5)
    m.graph_ndcg10 = ndcg_at_k(entities, relevant_entities, 10)

    # Qdrant evaluation
    sessions, q_time = extract_qdrant_sessions(indexer, gt.query)
    m.qdrant_retrieved = sessions
    m.qdrant_time_ms = q_time
    relevant_sessions = set(gt.expected_sessions)

    m.qdrant_mrr = reciprocal_rank(sessions, relevant_sessions)
    m.qdrant_p5 = precision_at_k(sessions, relevant_sessions, 5)
    m.qdrant_p10 = precision_at_k(sessions, relevant_sessions, 10)
    m.qdrant_r5 = recall_at_k(sessions, relevant_sessions, 5)
    m.qdrant_r10 = recall_at_k(sessions, relevant_sessions, 10)
    m.qdrant_ndcg5 = ndcg_at_k(sessions, relevant_sessions, 5)
    m.qdrant_ndcg10 = ndcg_at_k(sessions, relevant_sessions, 10)

    return m


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(metrics: list[QueryMetrics], label: str = "BASELINE") -> dict:
    """Print detailed report and return summary stats."""
    print(f"\n{'=' * 90}")
    print(f"  SEARCH QUALITY REPORT — {label}")
    print(f"{'=' * 90}")

    # Per-query details
    for i, m in enumerate(metrics, 1):
        print(f"\n{'─' * 80}")
        print(f"  Q{i:02d} [{m.category}]: \"{m.query}\"")
        print(f"{'─' * 80}")

        print(f"  GRAPH  | MRR={m.graph_mrr:.3f}  P@5={m.graph_p5:.3f}  R@5={m.graph_r5:.3f}  "
              f"NDCG@5={m.graph_ndcg5:.3f}  ({m.graph_time_ms:.0f}ms)")
        print(f"           Retrieved: {m.graph_retrieved[:7]}")

        print(f"  QDRANT | MRR={m.qdrant_mrr:.3f}  P@5={m.qdrant_p5:.3f}  R@5={m.qdrant_r5:.3f}  "
              f"NDCG@5={m.qdrant_ndcg5:.3f}  ({m.qdrant_time_ms:.0f}ms)")
        print(f"           Retrieved: {m.qdrant_retrieved[:7]}")

    # Aggregate by system
    n = len(metrics)
    summary = {}
    for system in ["graph", "qdrant"]:
        avg = lambda attr: sum(getattr(m, f"{system}_{attr}") for m in metrics) / n
        s = {
            "mrr": avg("mrr"),
            "p5": avg("p5"),
            "p10": avg("p10"),
            "r5": avg("r5"),
            "r10": avg("r10"),
            "ndcg5": avg("ndcg5"),
            "ndcg10": avg("ndcg10"),
            "avg_ms": avg("time_ms"),
        }
        summary[system] = s

    print(f"\n{'=' * 90}")
    print(f"  AGGREGATE METRICS ({n} queries)")
    print(f"{'=' * 90}")
    print(f"{'':>10} | {'MRR':>6} | {'P@5':>6} | {'P@10':>6} | {'R@5':>6} | {'R@10':>6} | {'NDCG@5':>7} | {'Avg ms':>7}")
    print(f"  {'-' * 76}")
    for system in ["graph", "qdrant"]:
        s = summary[system]
        print(f"  {system:>8} | {s['mrr']:.4f} | {s['p5']:.4f} | {s['p10']:.4f} | "
              f"{s['r5']:.4f} | {s['r10']:.4f} | {s['ndcg5']:.5f} | {s['avg_ms']:.1f}")

    # Aggregate by category
    categories = sorted(set(m.category for m in metrics))
    if len(categories) > 1:
        print(f"\n  BY CATEGORY (Graph P@5 / Qdrant P@5):")
        for cat in categories:
            cat_metrics = [m for m in metrics if m.category == cat]
            nc = len(cat_metrics)
            g_p5 = sum(m.graph_p5 for m in cat_metrics) / nc
            q_p5 = sum(m.qdrant_p5 for m in cat_metrics) / nc
            g_r5 = sum(m.graph_r5 for m in cat_metrics) / nc
            q_r5 = sum(m.qdrant_r5 for m in cat_metrics) / nc
            print(f"    {cat:>15}: Graph P@5={g_p5:.3f} R@5={g_r5:.3f} | Qdrant P@5={q_p5:.3f} R@5={q_r5:.3f}")

    # Zero-hit queries
    zero_graph = [m for m in metrics if not m.graph_retrieved]
    zero_qdrant = [m for m in metrics if not m.qdrant_retrieved]
    if zero_graph:
        print(f"\n  Graph zero-hit queries ({len(zero_graph)}):")
        for m in zero_graph:
            print(f"    - \"{m.query}\"")
    if zero_qdrant:
        print(f"\n  Qdrant zero-hit queries ({len(zero_qdrant)}):")
        for m in zero_qdrant:
            print(f"    - \"{m.query}\"")

    print()
    return summary


def main():
    after_fix = "--after-fix" in sys.argv
    label = "AFTER FIX" if after_fix else "BASELINE"

    print(f"Search Quality Test — {label}")
    print("Initializing...")

    kg = KnowledgeGraph()
    items = kg._read_all()
    entity_count = sum(1 for i in items if i.get("type") == "entity")
    print(f"  Graph: {entity_count} entities")

    indexer = Indexer()
    try:
        indexer.initialize()
        info = indexer._client.get_collection(indexer._collection)
        print(f"  Qdrant: {info.points_count} points")
    except Exception as exc:
        print(f"  Qdrant init failed: {exc}")
        sys.exit(1)

    print(f"  Ground truth: {len(GROUND_TRUTH)} queries")
    print(f"\nRunning evaluations...")

    metrics = []
    for gt in GROUND_TRUTH:
        m = evaluate_query(kg, indexer, gt)
        metrics.append(m)

    summary = print_report(metrics, label)

    # Save results as JSON for before/after comparison
    out_path = Path(__file__).parent / f"search_quality_{label.lower().replace(' ', '_')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "label": label,
            "n_queries": len(metrics),
            "summary": summary,
            "per_query": [
                {
                    "query": m.query,
                    "category": m.category,
                    "graph_mrr": m.graph_mrr,
                    "graph_p5": m.graph_p5,
                    "graph_r5": m.graph_r5,
                    "graph_ndcg5": m.graph_ndcg5,
                    "qdrant_mrr": m.qdrant_mrr,
                    "qdrant_p5": m.qdrant_p5,
                    "qdrant_r5": m.qdrant_r5,
                    "qdrant_ndcg5": m.qdrant_ndcg5,
                    "graph_retrieved": m.graph_retrieved[:10],
                    "qdrant_retrieved": m.qdrant_retrieved[:10],
                }
                for m in metrics
            ],
        }, f, indent=2, ensure_ascii=False)

    print(f"Results saved to: {out_path}")


if __name__ == "__main__":
    main()
