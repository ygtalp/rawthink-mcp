"""Generate synthetic vault data for scale benchmarking.

Creates realistic entities, relations, and session markdown files
to test RAWThink at 10K entity / 500 session scale.

Usage:
  python -m tests.generate_synthetic_data [--output-dir /tmp/rawthink-bench]
  python -m tests.generate_synthetic_data --entities 10000 --sessions 500
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Vocabulary pools for realistic data generation
# ---------------------------------------------------------------------------

ENTITY_TYPES = ["concept", "decision", "rule", "insight", "question", "project"]
EPISTEMIC = ["assertion", "hypothesis", "speculation", None, None]  # None = omitted

TOPIC_POOLS = {
    "concept": [
        "entropy", "emergence", "recursion", "self-reference", "compression",
        "consciousness", "causality", "determinism", "free-will", "duality",
        "abstraction", "encapsulation", "modularity", "coherence", "resonance",
        "symmetry", "topology", "graph-theory", "information", "complexity",
        "evolution", "adaptation", "feedback-loop", "homeostasis", "gradient",
        "attention", "memory", "pattern-matching", "inference", "prediction",
        "embedding", "vector-space", "manifold", "projection", "transform",
        "protocol", "interface", "contract", "boundary", "composition",
        "scaling", "distribution", "replication", "consensus", "partition",
    ],
    "decision": [
        "database-choice", "framework-selection", "api-design", "auth-method",
        "deployment-strategy", "caching-layer", "queue-system", "storage-format",
        "testing-approach", "monitoring-stack", "ci-cd-pipeline", "branching-model",
        "error-handling", "logging-strategy", "serialization-format", "orm-choice",
        "microservice-boundary", "event-schema", "retry-policy", "rate-limiting",
    ],
    "rule": [
        "verify-before-stating", "name-things-precisely", "epistemic-transparency",
        "corrections-must-transform", "distinguish-layers", "speed-not-value",
        "anti-mechanical", "mcp-first", "single-source-of-truth", "atomic-writes",
        "idempotent-operations", "fail-fast", "graceful-degradation", "backpressure",
        "circuit-breaker", "bulkhead-isolation", "timeout-everywhere", "correlation-id",
    ],
    "insight": [
        "compounding-knowledge", "strange-loop-pattern", "convergent-evolution",
        "stigmergy-coordination", "temporal-causality", "belief-revision",
        "activation-decay", "hybrid-search-value", "graph-traversal-power",
        "explicit-contradictions", "knowledge-compilation", "incremental-indexing",
    ],
    "question": [
        "consciousness-substrate", "simulation-boundary", "free-will-mechanism",
        "emergence-threshold", "optimal-chunk-size", "graph-vs-vector-tradeoff",
        "scaling-limit", "temporal-resolution", "confidence-calibration",
        "cross-domain-transfer", "language-model-understanding", "agency-definition",
    ],
    "project": [
        "rawthink", "knowledge-compiler", "semantic-search", "side-project",
        "graph-explorer", "session-manager", "belief-tracker", "pattern-detector",
        "insight-miner", "context-bridge", "memory-consolidator", "query-optimizer",
    ],
}

OBSERVATION_TEMPLATES = [
    "Investigated {topic} in session {sid} — {conclusion}.",
    "Key finding: {topic} relates to {other_topic} through {mechanism}.",
    "Decision made: chose {choice_a} over {choice_b} because {reason}.",
    "{topic} shows {property} behavior at scale — measured {metric}.",
    "Contradiction found: previous belief about {topic} invalidated by {evidence}.",
    "Pattern observed: {topic} and {other_topic} exhibit convergent properties.",
    "User preference: {topic} should use {approach} pattern for consistency.",
    "Performance note: {topic} operation takes {latency}ms at current scale.",
    "Architecture constraint: {topic} depends on {dependency} being available.",
    "Open question: does {topic} hold when {condition} changes?",
]

MECHANISMS = [
    "feedback loops", "information compression", "emergent dynamics",
    "causal chains", "structural similarity", "temporal correlation",
    "shared constraints", "competitive exclusion", "cooperative dynamics",
    "phase transitions", "critical thresholds", "resonance patterns",
]

CONCLUSIONS = [
    "the pattern is consistent across domains",
    "more data needed before confirming",
    "initial hypothesis holds with caveats",
    "the relationship is stronger than expected",
    "this contradicts the earlier assumption",
    "the mechanism is simpler than theorized",
    "scaling behavior is sublinear",
    "the tradeoff favors simplicity",
]

SESSION_SECTIONS = [
    "## Discussion\n\n{content}",
    "### Human\n\n> {prompt}\n\n### Assistant\n\n{response}",
    "## Analysis\n\n{content}\n\n## Conclusions\n\n{conclusion}",
    "### Key Points\n\n{bullets}\n\n### Follow-up\n\n{followup}",
]


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def generate_entity_name(entity_type: str, index: int) -> str:
    pool = TOPIC_POOLS.get(entity_type, TOPIC_POOLS["concept"])
    base = random.choice(pool)
    suffix = random.choice(["", f"-{index % 100}", f"-v{index % 10}"])
    return f"{base}{suffix}"


def generate_observations(entity_name: str, count: int) -> list[dict]:
    obs = []
    for i in range(count):
        template = random.choice(OBSERVATION_TEMPLATES)
        text = template.format(
            topic=entity_name.replace("-", " "),
            other_topic=random.choice(TOPIC_POOLS["concept"]).replace("-", " "),
            sid=f"2026-{random.randint(1,12):02d}-{random.randint(1,28):02d}_{random.randint(1,5):03d}",
            conclusion=random.choice(CONCLUSIONS),
            mechanism=random.choice(MECHANISMS),
            choice_a=random.choice(["option-A", "approach-1", "pattern-X"]),
            choice_b=random.choice(["option-B", "approach-2", "pattern-Y"]),
            reason=random.choice(CONCLUSIONS),
            property=random.choice(["linear", "logarithmic", "exponential", "constant"]),
            metric=random.randint(1, 5000),
            evidence=f"session {random.randint(1,500)} findings",
            approach=random.choice(["streaming", "batch", "hybrid", "lazy"]),
            latency=random.randint(1, 500),
            dependency=random.choice(["Qdrant", "Ollama", "JSONL store", "MCP transport"]),
            condition=f"scale exceeds {random.choice(['1K', '10K', '100K'])} entries",
        )
        obs.append({
            "text": text,
            "created": f"2026-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "status": "active",
        })
    return obs


def generate_entities(n: int) -> list[dict]:
    entities = []
    names_seen: set[str] = set()
    for i in range(n):
        etype = random.choice(ENTITY_TYPES)
        name = generate_entity_name(etype, i)
        # Ensure unique names
        while name in names_seen:
            name = f"{name}-{i}"
        names_seen.add(name)

        obs_count = random.choices([1, 2, 3, 5, 8, 15, 25], weights=[10, 20, 25, 20, 15, 7, 3])[0]
        epistemic = random.choice(EPISTEMIC)

        entity = {
            "type": "entity",
            "name": name,
            "entityType": etype,
            "observations": generate_observations(name, obs_count),
            "activation": round(random.uniform(0.1, 1.0), 3),
            "last_accessed": f"2026-{random.randint(1,4):02d}-{random.randint(1,28):02d}",
        }
        if epistemic:
            entity["epistemic"] = epistemic
        entities.append(entity)

    return entities


def generate_relations(entities: list[dict], ratio: float = 0.8) -> list[dict]:
    """Generate relations between entities. ratio = relations per entity on average."""
    from rawthink_mcp.config import RELATION_TYPES

    names = [e["name"] for e in entities]
    n_relations = int(len(entities) * ratio)
    rel_types = list(RELATION_TYPES)
    relations = []
    seen: set[tuple] = set()

    for _ in range(n_relations):
        src = random.choice(names)
        dst = random.choice(names)
        if src == dst:
            continue
        rtype = random.choice(rel_types)
        key = (src, dst, rtype)
        if key not in seen:
            seen.add(key)
            relations.append({
                "type": "relation",
                "from": src,
                "to": dst,
                "relationType": rtype,
            })

    return relations


def generate_session_markdown(session_id: str, date: str) -> str:
    """Generate a realistic session markdown file."""
    tags = random.sample(["philosophy", "technical", "personal", "architecture",
                          "debugging", "design", "research", "review"], k=random.randint(1, 3))

    title = f"Session {session_id} — {random.choice(['Deep dive', 'Architecture review', 'Debug session', 'Brainstorm', 'Research', 'Planning', 'Analysis'])}"

    frontmatter = f'''---
id: "{session_id}"
title: "{title}"
date: {date}
tags: [{", ".join(f'"{t}"' for t in tags)}]
status: completed
---'''

    # Generate 3-8 sections of content
    n_sections = random.randint(3, 8)
    sections = []
    for j in range(n_sections):
        speaker = random.choice(["### Human", "### Assistant"])
        # Content: 2-6 paragraphs, each 50-200 words
        paragraphs = []
        for _ in range(random.randint(2, 6)):
            words = random.randint(50, 200)
            # Generate plausible text from vocabulary
            vocab = (TOPIC_POOLS["concept"] + TOPIC_POOLS["decision"] +
                     list(MECHANISMS) + list(CONCLUSIONS))
            text = " ".join(random.choices(vocab, k=words))
            paragraphs.append(text)

        sections.append(f"\n{speaker}\n\n" + "\n\n".join(paragraphs))

    return frontmatter + "\n" + "\n".join(sections) + "\n"


def generate_qnote_markdown(qnote_id: str, date: str, session_ref: str) -> str:
    """Generate a realistic qnote markdown file."""
    tags = random.sample(["insight", "decision", "question", "pattern",
                          "architecture", "todo", "review"], k=random.randint(1, 2))

    content_lines = []
    n_lines = random.randint(2, 8)
    for _ in range(n_lines):
        template = random.choice(OBSERVATION_TEMPLATES)
        line = template.format(
            topic=random.choice(TOPIC_POOLS["concept"]),
            other_topic=random.choice(TOPIC_POOLS["concept"]),
            sid=session_ref,
            conclusion=random.choice(CONCLUSIONS),
            mechanism=random.choice(MECHANISMS),
            choice_a="approach-A",
            choice_b="approach-B",
            reason=random.choice(CONCLUSIONS),
            property=random.choice(["linear", "sublinear", "constant"]),
            metric=random.randint(1, 500),
            evidence="new findings",
            approach=random.choice(["streaming", "batch"]),
            latency=random.randint(1, 100),
            dependency="core system",
            condition="scale increases",
        )
        content_lines.append(line)

    return f"""---
id: "{qnote_id}"
date: {date}
tags: [{", ".join(f'"{t}"' for t in tags)}]
session_ref: "{session_ref}"
---

{"  ".join(content_lines)}
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate synthetic RAWThink data for benchmarking")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: tests/bench_data)")
    parser.add_argument("--entities", type=int, default=10000, help="Number of entities")
    parser.add_argument("--sessions", type=int, default=500, help="Number of sessions")
    parser.add_argument("--qnotes", type=int, default=200, help="Number of qnotes")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    random.seed(args.seed)

    output_dir = Path(args.output_dir) if args.output_dir else Path(__file__).parent / "bench_data"
    vault_dir = output_dir / "vault"
    sessions_dir = vault_dir / "sessions"
    qnotes_dir = vault_dir / "qnotes"

    for d in [sessions_dir, qnotes_dir]:
        d.mkdir(parents=True, exist_ok=True)

    print(f"Generating synthetic data (seed={args.seed})...")
    print(f"  Output: {output_dir}")

    # --- Entities ---
    print(f"  Generating {args.entities} entities...")
    entities = generate_entities(args.entities)

    # --- Relations ---
    print(f"  Generating relations...")
    relations = generate_relations(entities, ratio=0.8)
    print(f"    {len(relations)} relations created")

    # --- Write JSONL ---
    memory_path = vault_dir / "memory.jsonl"
    items = entities + relations
    with open(memory_path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    file_size_mb = memory_path.stat().st_size / (1024 * 1024)
    print(f"    memory.jsonl: {file_size_mb:.1f} MB ({len(entities)} entities + {len(relations)} relations)")

    # --- Observation stats ---
    obs_counts = [len(e.get("observations", [])) for e in entities]
    total_obs = sum(obs_counts)
    print(f"    Total observations: {total_obs} (avg {total_obs/len(entities):.1f} per entity)")

    # --- Sessions ---
    print(f"  Generating {args.sessions} sessions...")
    session_ids = []
    for i in range(args.sessions):
        month = (i // 50) % 12 + 1
        day = (i % 28) + 1
        seq = (i % 5) + 1
        date = f"2026-{month:02d}-{day:02d}"
        sid = f"{date}_{seq:03d}"
        session_ids.append(sid)

        content = generate_session_markdown(sid, date)
        fname = f"{sid}_bench-session.md"
        (sessions_dir / fname).write_text(content, encoding="utf-8")

    # --- Qnotes ---
    print(f"  Generating {args.qnotes} qnotes...")
    for i in range(args.qnotes):
        month = random.randint(1, 4)
        day = random.randint(1, 28)
        date = f"2026-{month:02d}-{day:02d}"
        ts = f"{random.randint(0,23):02d}{random.randint(0,59):02d}{random.randint(0,59):02d}"
        qid = f"qnote_{date}_{ts}"
        session_ref = random.choice(session_ids)

        content = generate_qnote_markdown(qid, date, session_ref)
        fname = f"{date}_{ts}.md"
        (qnotes_dir / fname).write_text(content, encoding="utf-8")

    # --- Summary ---
    total_md_files = args.sessions + args.qnotes
    total_md_size = sum(f.stat().st_size for f in vault_dir.rglob("*.md")) / (1024 * 1024)
    print(f"\n  Summary:")
    print(f"    Entities:    {len(entities):>8,}")
    print(f"    Relations:   {len(relations):>8,}")
    print(f"    Observations:{total_obs:>8,}")
    print(f"    Sessions:    {args.sessions:>8}")
    print(f"    Qnotes:      {args.qnotes:>8}")
    print(f"    memory.jsonl: {file_size_mb:>7.1f} MB")
    print(f"    Markdown:     {total_md_size:>7.1f} MB ({total_md_files} files)")
    print(f"\n  Done. Output: {output_dir}")


if __name__ == "__main__":
    main()
