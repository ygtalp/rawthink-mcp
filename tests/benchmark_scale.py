"""Scale benchmark for RAWThink — measures performance at 10K entity / 500 session scale.

Tests graph operations (JSONL parse, index build, search, write) and
Qdrant operations (indexing, hybrid search) with synthetic data.

Usage:
  # First generate data:
  python -m tests.generate_synthetic_data

  # Then benchmark (graph-only, no Qdrant/Ollama needed):
  python -m tests.benchmark_scale

  # Full benchmark including Qdrant (requires Docker + Ollama):
  python -m tests.benchmark_scale --with-qdrant

Reports p50/p95/p99 latencies, throughput, and memory usage.
"""
from __future__ import annotations

import gc
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rawthink_mcp.graph import KnowledgeGraph


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

def percentile(data: list[float], p: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[f]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    else:
        return f"{n / (1024 ** 2):.1f} MB"


# ---------------------------------------------------------------------------
# Graph benchmarks
# ---------------------------------------------------------------------------

SEARCH_QUERIES = [
    "consciousness self reference",
    "database choice scaling",
    "verify before stating",
    "compounding knowledge pattern",
    "entropy emergence",
    "vector space embedding",
    "protocol interface contract",
    "feedback loop homeostasis",
    "architecture review decision",
    "temporal causality",
    "hybrid search value",
    "recursion abstraction",
    "attention memory prediction",
    "scaling distribution replication",
    "gradient adaptation evolution",
    "modularity encapsulation boundary",
    "activation decay",
    "belief revision",
    "convergent evolution pattern",
    "graph traversal power",
]


def bench_graph_load(memory_path: str, rounds: int = 5) -> dict:
    """Benchmark: JSONL parse + index build time."""
    times = []
    entity_count = 0
    relation_count = 0
    file_size = os.path.getsize(memory_path)

    for _ in range(rounds):
        kg = KnowledgeGraph(path=memory_path)
        gc.collect()
        start = time.perf_counter()
        items = kg._read_all()  # noqa: SLF001
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
        entity_count = sum(1 for i in items if i.get("type") == "entity")
        relation_count = sum(1 for i in items if i.get("type") == "relation")

    return {
        "operation": "graph_load (parse + index build)",
        "entities": entity_count,
        "relations": relation_count,
        "file_size": format_bytes(file_size),
        "rounds": rounds,
        "p50_ms": round(percentile(times, 50), 1),
        "p95_ms": round(percentile(times, 95), 1),
        "p99_ms": round(percentile(times, 99), 1),
        "mean_ms": round(statistics.mean(times), 1),
    }


def bench_search_nodes(memory_path: str, rounds: int = 3) -> dict:
    """Benchmark: search_nodes latency (includes read + index + search + touch + write)."""
    # Pre-warm: first load
    kg = KnowledgeGraph(path=memory_path)
    kg._read_all()  # noqa: SLF001

    all_times = []
    for _ in range(rounds):
        for query in SEARCH_QUERIES:
            # Each search_nodes call reads file, builds index, searches, touches, writes
            kg_fresh = KnowledgeGraph(path=memory_path)
            gc.collect()
            start = time.perf_counter()
            result = kg_fresh.search_nodes(query)
            elapsed = (time.perf_counter() - start) * 1000
            all_times.append(elapsed)

    return {
        "operation": "search_nodes (full cycle: read + index + search + touch + write)",
        "queries": len(SEARCH_QUERIES),
        "rounds": rounds,
        "total_searches": len(all_times),
        "p50_ms": round(percentile(all_times, 50), 1),
        "p95_ms": round(percentile(all_times, 95), 1),
        "p99_ms": round(percentile(all_times, 99), 1),
        "mean_ms": round(statistics.mean(all_times), 1),
        "min_ms": round(min(all_times), 1),
        "max_ms": round(max(all_times), 1),
    }


def bench_search_nodes_cached(memory_path: str) -> dict:
    """Benchmark: search_nodes with pre-warmed cache (simulates consecutive calls)."""
    kg = KnowledgeGraph(path=memory_path)
    # Pre-warm cache
    kg._read_all()  # noqa: SLF001

    # Patch _write_all to skip disk writes for read-only benchmark
    original_write = kg._write_all
    kg._write_all = lambda items: None  # noqa: SLF001

    times = []
    for query in SEARCH_QUERIES:
        # Force re-read to simulate real behavior
        kg._cached_items = None  # noqa: SLF001
        kg._index = None  # noqa: SLF001
        start = time.perf_counter()
        kg.search_nodes(query)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    kg._write_all = original_write  # noqa: SLF001

    return {
        "operation": "search_nodes (no disk write — read + index + search only)",
        "queries": len(times),
        "p50_ms": round(percentile(times, 50), 1),
        "p95_ms": round(percentile(times, 95), 1),
        "p99_ms": round(percentile(times, 99), 1),
        "mean_ms": round(statistics.mean(times), 1),
    }


def bench_create_entities(memory_path: str, count: int = 100) -> dict:
    """Benchmark: creating new entities (includes full read + write cycle)."""
    import shutil
    import tempfile

    # Work on a copy
    tmp_dir = tempfile.mkdtemp(prefix="rawthink-bench-")
    tmp_path = os.path.join(tmp_dir, "memory.jsonl")
    shutil.copy2(memory_path, tmp_path)

    kg = KnowledgeGraph(path=tmp_path)

    entities = [
        {
            "name": f"bench-entity-{i}",
            "entityType": "concept",
            "observations": [f"Benchmark observation {i}"],
        }
        for i in range(count)
    ]

    # Batch create
    gc.collect()
    start = time.perf_counter()
    kg.create_entities(entities)
    elapsed = (time.perf_counter() - start) * 1000

    # Cleanup
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return {
        "operation": f"create_entities (batch of {count})",
        "total_ms": round(elapsed, 1),
        "per_entity_ms": round(elapsed / count, 2),
    }


def bench_read_graph(memory_path: str, rounds: int = 3) -> dict:
    """Benchmark: read_graph (full load, no filtering)."""
    times = []
    for _ in range(rounds):
        kg = KnowledgeGraph(path=memory_path)
        gc.collect()
        start = time.perf_counter()
        result = kg.read_graph()
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    return {
        "operation": "read_graph (full load)",
        "rounds": rounds,
        "p50_ms": round(percentile(times, 50), 1),
        "p95_ms": round(percentile(times, 95), 1),
        "mean_ms": round(statistics.mean(times), 1),
    }


def bench_open_nodes(memory_path: str) -> dict:
    """Benchmark: open_nodes for specific entities."""
    kg = KnowledgeGraph(path=memory_path)
    items = kg._read_all()  # noqa: SLF001
    entity_names = [e["name"] for e in items if e.get("type") == "entity"]

    # Pick 20 random entities to open
    import random
    random.seed(99)
    targets = random.sample(entity_names, min(20, len(entity_names)))

    # Patch write to avoid disk churn
    kg._write_all = lambda items: None  # noqa: SLF001

    times = []
    for name in targets:
        kg._cached_items = None  # noqa: SLF001
        kg._index = None  # noqa: SLF001
        start = time.perf_counter()
        kg.open_nodes([name])
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    return {
        "operation": "open_nodes (single entity, includes full load)",
        "queries": len(times),
        "p50_ms": round(percentile(times, 50), 1),
        "p95_ms": round(percentile(times, 95), 1),
        "mean_ms": round(statistics.mean(times), 1),
    }


# ---------------------------------------------------------------------------
# Qdrant benchmarks (optional)
# ---------------------------------------------------------------------------

def bench_qdrant_index(vault_path: str) -> dict | None:
    """Benchmark: full vault indexing into Qdrant."""
    try:
        from rawthink_mcp.indexer import Indexer
    except ImportError:
        return None

    indexer = Indexer()
    try:
        indexer.initialize()
    except Exception as exc:
        return {"operation": "qdrant_index", "error": str(exc)}

    gc.collect()
    start = time.perf_counter()
    count = indexer.index_vault()
    elapsed = (time.perf_counter() - start)

    return {
        "operation": "qdrant_index_vault (embed + upsert)",
        "chunks_indexed": count,
        "total_seconds": round(elapsed, 1),
        "chunks_per_second": round(count / elapsed, 1) if elapsed > 0 else 0,
    }


def bench_qdrant_search(vault_path: str, rounds: int = 3) -> dict | None:
    """Benchmark: hybrid search latency."""
    try:
        from rawthink_mcp.indexer import Indexer, EmbeddingError
    except ImportError:
        return None

    indexer = Indexer()
    try:
        indexer.initialize()
    except Exception as exc:
        return {"operation": "qdrant_search", "error": str(exc)}

    times = []
    for _ in range(rounds):
        for query in SEARCH_QUERIES:
            start = time.perf_counter()
            try:
                indexer.search(query=query, limit=10)
            except EmbeddingError:
                continue
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)

    if not times:
        return {"operation": "qdrant_hybrid_search", "error": "All searches failed (Ollama unavailable?)"}

    return {
        "operation": "qdrant_hybrid_search (embed + prefetch + RRF)",
        "queries": len(times),
        "p50_ms": round(percentile(times, 50), 1),
        "p95_ms": round(percentile(times, 95), 1),
        "p99_ms": round(percentile(times, 99), 1),
        "mean_ms": round(statistics.mean(times), 1),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(results: list[dict], data_summary: dict) -> None:
    print(f"\n{'=' * 90}")
    print(f"  RAWTHINK SCALE BENCHMARK")
    print(f"{'=' * 90}")
    print(f"  Data: {data_summary.get('entities', '?')} entities, "
          f"{data_summary.get('relations', '?')} relations, "
          f"{data_summary.get('observations', '?')} observations")
    print(f"  JSONL: {data_summary.get('file_size', '?')}")
    print(f"  Sessions: {data_summary.get('sessions', '?')}, "
          f"Qnotes: {data_summary.get('qnotes', '?')}")
    print(f"{'=' * 90}")

    for r in results:
        print(f"\n  {r.get('operation', '?')}")
        print(f"  {'-' * 70}")
        for k, v in r.items():
            if k == "operation":
                continue
            print(f"    {k:>25}: {v}")

    # Bottleneck analysis
    print(f"\n{'=' * 90}")
    print(f"  BOTTLENECK ANALYSIS")
    print(f"{'=' * 90}")

    search_full = next((r for r in results if "full cycle" in r.get("operation", "")), None)
    search_readonly = next((r for r in results if "no disk write" in r.get("operation", "")), None)
    load = next((r for r in results if "graph_load" in r.get("operation", "")), None)

    if search_full and search_readonly and load:
        full_p50 = search_full["p50_ms"]
        readonly_p50 = search_readonly["p50_ms"]
        load_p50 = load["p50_ms"]
        write_overhead = full_p50 - readonly_p50

        print(f"\n  Per search_nodes call (p50):")
        print(f"    JSONL parse + index build:  {load_p50:>8.1f} ms")
        print(f"    Search + match:             {readonly_p50 - load_p50:>8.1f} ms")
        print(f"    Disk write (activation):    {write_overhead:>8.1f} ms")
        print(f"    Total:                      {full_p50:>8.1f} ms")
        print(f"\n  Write overhead: {write_overhead / full_p50 * 100:.0f}% of total search time")

        if full_p50 > 500:
            print(f"\n  WARNING: Search latency >{full_p50:.0f}ms — consider:")
            print(f"    - Lazy activation writes (batch, not per-search)")
            print(f"    - Persistent in-memory cache across calls")
            print(f"    - Separate hot path (search) from cold path (write)")
        elif full_p50 > 100:
            print(f"\n  NOTE: Search latency {full_p50:.0f}ms — acceptable for MCP tool response")
            print(f"    but activation write overhead ({write_overhead:.0f}ms) is optimizable")
        else:
            print(f"\n  PASS: Search latency {full_p50:.0f}ms — well within MCP response budget")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="RAWThink scale benchmark")
    parser.add_argument("--data-dir", default=None, help="Directory with synthetic data (default: tests/bench_data)")
    parser.add_argument("--with-qdrant", action="store_true", help="Include Qdrant benchmarks (needs Docker + Ollama)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).parent / "bench_data"
    vault_dir = data_dir / "vault"
    memory_path = str(vault_dir / "memory.jsonl")

    if not os.path.exists(memory_path):
        print(f"ERROR: No benchmark data found at {data_dir}")
        print(f"Run first: python -m tests.generate_synthetic_data")
        sys.exit(1)

    # Gather data summary
    file_size = os.path.getsize(memory_path)
    with open(memory_path, "r", encoding="utf-8") as f:
        items = [json.loads(line) for line in f if line.strip()]
    entities = [i for i in items if i.get("type") == "entity"]
    relations = [i for i in items if i.get("type") == "relation"]
    total_obs = sum(len(e.get("observations", [])) for e in entities)

    sessions_dir = vault_dir / "sessions"
    qnotes_dir = vault_dir / "qnotes"
    n_sessions = len(list(sessions_dir.glob("*.md"))) if sessions_dir.exists() else 0
    n_qnotes = len(list(qnotes_dir.glob("*.md"))) if qnotes_dir.exists() else 0

    # Set config to use synthetic data
    import rawthink_mcp.config as cfg
    cfg.VAULT_PATH = str(vault_dir)
    cfg.MEMORY_FILE = memory_path

    data_summary = {
        "entities": len(entities),
        "relations": len(relations),
        "observations": total_obs,
        "file_size": format_bytes(file_size),
        "sessions": n_sessions,
        "qnotes": n_qnotes,
    }

    print(f"RAWThink Scale Benchmark")
    print(f"Data: {data_dir}")
    print(f"  {len(entities)} entities, {len(relations)} relations, {total_obs} observations")
    print(f"  JSONL: {format_bytes(file_size)}")
    print(f"  {n_sessions} sessions, {n_qnotes} qnotes\n")

    results = []

    # Graph benchmarks
    print("Running graph benchmarks...")

    print("  [1/5] graph_load...")
    results.append(bench_graph_load(memory_path))

    print("  [2/5] search_nodes (full cycle)...")
    results.append(bench_search_nodes(memory_path, rounds=2))

    print("  [3/5] search_nodes (read-only)...")
    results.append(bench_search_nodes_cached(memory_path))

    print("  [4/5] create_entities...")
    results.append(bench_create_entities(memory_path, count=100))

    print("  [5/5] read_graph + open_nodes...")
    results.append(bench_read_graph(memory_path))
    results.append(bench_open_nodes(memory_path))

    # Qdrant benchmarks (optional)
    if args.with_qdrant:
        print("\nRunning Qdrant benchmarks...")
        print("  [1/2] index_vault...")
        r = bench_qdrant_index(str(vault_dir))
        if r:
            results.append(r)

        print("  [2/2] hybrid_search...")
        r = bench_qdrant_search(str(vault_dir))
        if r:
            results.append(r)

    # Report
    print_report(results, data_summary)

    # Save results
    out_path = Path(__file__).parent / "benchmark_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "data_summary": data_summary,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"Results saved to: {out_path}")


if __name__ == "__main__":
    main()
