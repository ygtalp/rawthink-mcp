"""Concurrency and atomicity locks (A4, A4b, A7, A15, A17, A5).

The lost-update test uses subprocesses, not threads. filelock is reentrant
within one process, so a thread-based test passes whether or not the lock
works — it would certify the bug rather than catch it.
"""
import json
import os
import subprocess
import sys
import textwrap

from pathlib import Path

import pytest

# Repo root, derived rather than hardcoded: these tests spawn subprocesses that
# need to import the package under test, and an absolute path baked in at
# authoring time only works on the machine it was written on.
SRC = str(Path(__file__).resolve().parents[2])

WRITER = textwrap.dedent("""
    import sys
    sys.path.insert(0, {src!r})
    from rawthink_mcp.graph import KnowledgeGraph
    kg = KnowledgeGraph(sys.argv[1])
    tag = sys.argv[2]
    for i in range(12):
        kg.record(entities=[{{"name": f"{{tag}}-{{i}}", "entityType": "concept",
                             "domain": "software", "observations": [f"note {{i}}"]}}])
""")


def _spawn(path, tag, tmp_path):
    script = tmp_path / f"w_{tag}.py"
    script.write_text(WRITER.format(src=SRC))
    return subprocess.Popen([sys.executable, str(script), str(path), tag])


@pytest.mark.slow
def test_concurrent_writers_do_not_lose_updates(vault, tmp_path):
    """Two processes writing the same graph must both survive.

    Without a lock the second writer reads before the first has written and
    overwrites it — silently, because nothing errors.
    """
    path = vault / "memory.jsonl"
    procs = [_spawn(path, tag, tmp_path) for tag in ("alpha", "beta", "gamma")]
    for p in procs:
        assert p.wait(timeout=120) == 0

    items = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    names = {i["name"] for i in items if i.get("type") == "entity"}
    for tag in ("alpha", "beta", "gamma"):
        got = {n for n in names if n.startswith(tag)}
        assert len(got) == 12, f"{tag}: {len(got)}/12 survived — updates were lost"


def test_write_leaves_no_temp_files(kg, vault):
    """A fixed .jsonl.tmp name let two writers clobber each other's temp file."""
    kg.record(entities=[{"name": "a", "entityType": "concept", "domain": "music"}])
    leftovers = [n for n in os.listdir(vault) if n.endswith(".tmp")]
    assert not leftovers, f"temp files left behind: {leftovers}"


def test_reading_does_not_modify_the_file(kg, vault):
    """open_nodes used to write on the read path, so reading a git-tracked file
    produced a diff — which defeats the reason it is JSONL."""
    kg.record(entities=[{"name": "a", "entityType": "concept", "domain": "music"}])
    path = vault / "memory.jsonl"
    before = (path.stat().st_mtime_ns, path.read_bytes())
    kg.open_nodes(["a"])
    kg.search_nodes("a")
    kg.read_graph()
    after = (path.stat().st_mtime_ns, path.read_bytes())
    assert before == after, "a read path wrote to the graph"


def test_cache_is_reused_until_the_file_changes(kg, vault):
    """The cache was written but never read, so every call re-parsed the file."""
    kg.record(entities=[{"name": "a", "entityType": "concept", "domain": "music"}])
    first = kg._read_all()
    assert kg._cached_items is not None and kg._cache_sig is not None
    second = kg._read_all()
    assert second is first, "cache was not reused"
    kg.record(entities=[{"name": "b", "entityType": "concept", "domain": "music"}])
    assert kg._read_all() is not first, "cache survived a write"


def test_empty_item_list_is_not_treated_as_missing(kg):
    """`items or self._read_all()` re-read on [], because [] is falsy."""
    assert kg._entities([]) == []
    assert kg._relations([]) == []


def test_delete_reports_only_what_existed(kg):
    """It used to claim it deleted names that were never there."""
    kg.record(entities=[{"name": "real", "entityType": "concept", "domain": "music"}])
    assert kg.delete_entities(["real", "ghost"]) == ["real"]


def test_search_matches_prefix_not_substring(kg):
    """'art' matched 'smart' before, and every query token scanned the whole
    vocabulary to find out."""
    kg.record(entities=[
        {"name": "smart-thing", "entityType": "concept", "domain": "software"},
        {"name": "artifact-store", "entityType": "artifact", "domain": "software"}])
    names = {e["name"] for e in kg.search_nodes("art", limit=10)["entities"]}
    assert "artifact-store" in names
    assert "smart-thing" not in names
