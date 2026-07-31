"""Graceful shutdown locks.

Qdrant in embedded mode holds a directory lock and the graph holds a file lock.
A process that exits without releasing them leaves the next start failing on a
lock error that reads like corruption.
"""
import os
import signal
import subprocess
import sys
import textwrap

from pathlib import Path

import pytest

# Repo root, derived rather than hardcoded: these tests spawn subprocesses that
# need to import the package under test, and an absolute path baked in at
# authoring time only works on the machine it was written on.
SRC = str(Path(__file__).resolve().parents[2])


def test_indexer_close_releases_the_client(vault, stub_embedder):
    from rawthink_mcp.indexer import Indexer
    idx = Indexer(qdrant_path=str(vault / "qd"), embedder=stub_embedder,
                  bm25_state_path=str(vault / "state.json"))
    idx.initialize()
    assert idx._client is not None
    idx.close()
    assert idx._client is None


def test_indexer_close_is_idempotent(vault, stub_embedder):
    from rawthink_mcp.indexer import Indexer
    idx = Indexer(qdrant_path=":memory:", embedder=stub_embedder,
                  bm25_state_path=str(vault / "state.json"))
    idx.initialize()
    idx.close()
    idx.close()   # must not raise


def test_graph_close_releases_the_file_lock(kg):
    kg.record(entities=[{"name": "a", "entityType": "concept", "domain": "music"}])
    kg.close()
    assert not kg._flock.is_locked


def test_embedded_qdrant_can_be_reopened_after_close(vault, stub_embedder):
    """The real symptom: a second start fails while the first still holds the
    storage directory."""
    from rawthink_mcp.indexer import Indexer
    path = str(vault / "qd")
    first = Indexer(qdrant_path=path, embedder=stub_embedder,
                    bm25_state_path=str(vault / "s.json"))
    first.initialize()
    first.close()
    second = Indexer(qdrant_path=path, embedder=stub_embedder,
                     bm25_state_path=str(vault / "s.json"))
    second.initialize()          # would raise if the lock were still held
    second.close()


def test_shutdown_is_safe_with_nothing_initialized(monkeypatch):
    import rawthink_mcp.server as srv
    monkeypatch.setattr(srv, "_indexer", None)
    monkeypatch.setattr(srv, "_graph", None)
    srv._shutdown()   # must not raise


@pytest.mark.slow
@pytest.mark.skipif(os.name == "nt", reason="Windows terminates rather than signalling")
def test_sigterm_exits_cleanly(vault, tmp_path):
    """SIGTERM should unwind rather than kill — docker stop, or a terminated
    Claude Code session, must not leave locks behind.

    POSIX only. On Windows `send_signal(SIGTERM)` calls TerminateProcess: the
    process dies immediately and no Python handler runs. That is a real gap,
    not a test artefact — see the note on _install_signal_handlers.
    """
    script = tmp_path / "run.py"
    script.write_text(textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {SRC!r})
        import rawthink_mcp.server as srv
        srv._install_signal_handlers()
        print("ready", flush=True)
        try:
            while True:
                time.sleep(0.05)
        finally:
            srv._shutdown()
    """))
    p = subprocess.Popen([sys.executable, str(script)],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "ready"
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=15) == 0, "SIGTERM should produce a clean exit code"
