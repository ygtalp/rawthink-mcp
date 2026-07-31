"""rawthink-doctor — check that an installation is actually working.

Setup has heavy external dependencies (Docker, Ollama, a vault on disk) and
most of the ways it breaks are quiet: an index that stopped halfway, a BM25
state file from before the term-ID fix, a collection whose vector size no
longer matches the model. None of these raise anything. Search just gets
worse, and there is no moment where you find out.

Each check prints a status and, when it fails, the command that fixes it.

    rawthink-doctor
    rawthink-doctor --vault ~/rawthink-vault/vault
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import config

OK, WARN, FAIL = "[x]", "[!]", "[ ]"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "", fix: str = "") -> None:
        self.rows.append((status, name, detail, fix))

    def __init_subclass__(cls): ...

    def render(self, header: str = "") -> int:
        print("\nrawthink-doctor")
        if header:
            print(header)
        print()
        for status, name, detail, fix in self.rows:
            print(f"  {status} {name}" + (f" — {detail}" if detail else ""))
            if status != OK and fix:
                for line in fix.splitlines():
                    print(f"        {line}")
        failed = sum(1 for r in self.rows if r[0] == FAIL)
        warned = sum(1 for r in self.rows if r[0] == WARN)
        passed = len(self.rows) - failed - warned
        print(f"\n  {passed} ok, {warned} warning, {failed} failed\n")
        return 1 if failed else 0


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_vault(rep: Report, vault: Path) -> None:
    if not vault.is_dir():
        rep.add(FAIL, "vault directory", str(vault),
                "rawthink-install --vault <path>")
        return
    try:
        probe = vault / ".rawthink-doctor-write-test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        rep.add(FAIL, "vault writable", str(exc),
                f"chmod u+w {vault}")
        return
    sessions = len(list((vault / "sessions").glob("*.md"))) if (vault / "sessions").is_dir() else 0
    qnotes = len(list((vault / "qnotes").glob("*.md"))) if (vault / "qnotes").is_dir() else 0
    if sessions == 0 and qnotes == 0:
        # An empty vault and the wrong vault look identical from here, and the
        # default resolves next to the package — which is not where a vault
        # usually lives. Say so rather than reporting a confident zero.
        rep.add(WARN, "vault directory", "empty (0 sessions, 0 qnotes)",
                "If this is not the vault you meant:\n"
                "  rawthink-doctor --vault <path>\n"
                "  or set RAWTHINK_VAULT")
        return
    rep.add(OK, "vault directory", f"{sessions} sessions, {qnotes} qnotes")


def check_graph(rep: Report, memory_file: Path) -> None:
    if not memory_file.exists():
        rep.add(WARN, "knowledge graph", "no memory.jsonl yet",
                "It is created on the first write. Nothing to do if this is a "
                "fresh install.")
        return
    entities = relations = bad = 0
    bad_types: set[str] = set()
    bad_rels: set[str] = set()
    for line in memory_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if item.get("type") == "entity":
            entities += 1
            if item.get("entityType") not in config.ENTITY_TYPES:
                bad_types.add(item.get("entityType", "(missing)"))
        elif item.get("type") == "relation":
            relations += 1
            if item.get("relationType") not in config.RELATION_TYPES:
                bad_rels.add(item.get("relationType", "(missing)"))

    if bad:
        rep.add(FAIL, "graph file", f"{bad} unparseable lines",
                f"Inspect {memory_file}; restore from a .bak-* backup if needed.")
        return

    if bad_types or bad_rels:
        detail = f"{len(bad_types)} unknown entity types, {len(bad_rels)} unknown relation types"
        rep.add(FAIL, "graph schema", detail,
                "The graph predates the controlled vocabulary. Migrate it:\n"
                "  python -m rawthink_mcp.migrate --guess-domains --heal-dangling\n"
                "  python -m rawthink_mcp.migrate --guess-domains --heal-dangling --apply")
        return

    names = set()
    dangling = set()
    for line in memory_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if item.get("type") == "entity":
                names.add(item["name"])
    for line in memory_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if item.get("type") == "relation":
                dangling |= {n for n in (item["from"], item["to"]) if n not in names}
    if dangling:
        rep.add(WARN, "graph edges", f"{len(dangling)} relations point at unknown entities",
                "These edges are not traversable. Repair them:\n"
                "  python -m rawthink_mcp.migrate --heal-dangling --apply")
        return

    rep.add(OK, "knowledge graph", f"{entities} entities, {relations} relations")


def check_bm25_state(rep: Report, path: Path, vault: Path | None = None) -> None:
    if not path.exists():
        rep.add(WARN, "BM25 state", "not built yet",
                "Sparse retrieval is inactive until the vault is indexed:\n"
                "  python -m rawthink_mcp.indexer --index-vault")
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        rep.add(FAIL, "BM25 state", "unparseable", f"Delete {path} and reindex.")
        return
    tokens = len(state.get("vocab", {}))
    # Only meaningful when a vault was given to compare against. Called without
    # one, this check has nothing to be inconsistent with.
    if vault is not None and (vault / "sessions").is_dir():
        sessions = len(list((vault / "sessions").glob("*.md")))
    else:
        sessions = None
    if sessions == 0 and tokens > 0:
        rep.add(WARN, "BM25 state", f"{tokens} tokens for an empty vault",
                "State and vault disagree — this file was probably written by a\n"
                "different vault, or by a test run. Check which vault you meant,\n"
                f"or remove it:\n  rm {path}")
        return

    version = state.get("format_version", 1)
    if version < 2:
        rep.add(FAIL, "BM25 state", f"format_version={version} (legacy term IDs)",
                "Term IDs were corpus-dependent in that format, so sparse\n"
                "vectors no longer match queries. Delete it and re-encode:\n"
                f"  rm {path}\n"
                "  then call reindex(full=True), or:\n"
                "  python -m rawthink_mcp.indexer --index-vault")
        return
    legacy = Path(__file__).parent / ".bm25_state.json"
    if legacy.exists():
        rep.add(WARN, "BM25 state location", "a copy remains in the package directory",
                f"Every vault on this machine would share it. Remove:\n  rm {legacy}")
        return
    rep.add(OK, "BM25 state", f"format_version={version}, {len(state.get('vocab', {}))} tokens")


def check_qdrant(rep: Report) -> object | None:
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        rep.add(FAIL, "qdrant-client", "not installed", "pip install rawthink-mcp")
        return None
    try:
        client = (QdrantClient(path=config.QDRANT_PATH) if config.QDRANT_PATH
                  else QdrantClient(url=config.QDRANT_URL))
        client.get_collections()
    except Exception as exc:
        where = config.QDRANT_PATH or config.QDRANT_URL
        rep.add(FAIL, "Qdrant reachable", f"{type(exc).__name__} at {where}",
                "Start it:\n  docker compose up -d\n"
                "or run embedded by setting QDRANT_PATH to a directory.")
        return None
    rep.add(OK, "Qdrant reachable", config.QDRANT_PATH or config.QDRANT_URL)
    return client


def check_collection(rep: Report, client, vault: Path) -> None:
    """The check that matters most, and the one that was missing.

    An index can stop partway through and leave the collection looking healthy:
    it exists, it has points, queries return results. They are just results
    from half the vault, and nothing anywhere says so.
    """
    if client is None:
        return
    name = config.COLLECTION_NAME
    try:
        if not client.collection_exists(name):
            rep.add(FAIL, "collection", f"'{name}' does not exist",
                    "Index the vault:\n  python -m rawthink_mcp.indexer --index-vault")
            return
        info = client.get_collection(name)
    except Exception as exc:
        rep.add(FAIL, "collection", str(exc)[:60])
        return

    vectors = getattr(info.config.params, "vectors", None) or {}
    dense = vectors.get("dense") if isinstance(vectors, dict) else None
    size = getattr(dense, "size", None)
    if size and size != config.VECTOR_DIM:
        rep.add(FAIL, "vector dimension", f"collection={size}, config={config.VECTOR_DIM}",
                "The collection was built with a different embedding model.\n"
                "Drop the collection and reindex, or set OLLAMA_MODEL back.")
        return

    # --- index coverage ---
    indexed: set[str] = set()
    offset = None
    try:
        while True:
            batch, offset = client.scroll(name, limit=1000, offset=offset,
                                          with_payload=["session_id"], with_vectors=False)
            for point in batch:
                sid = (point.payload or {}).get("session_id", "")
                if sid:
                    indexed.add(sid)
            if offset is None:
                break
    except Exception as exc:
        rep.add(WARN, "index coverage", f"could not scroll: {type(exc).__name__}")
        return

    session_dir = vault / "sessions"
    on_disk = set()
    if session_dir.is_dir():
        for f in session_dir.glob("*.md"):
            parts = f.stem.split("_")
            if len(parts) >= 2:
                on_disk.add(f"{parts[0]}_{parts[1]}")

    missing = on_disk - indexed
    if missing:
        newest = sorted(missing)[-1]
        rep.add(FAIL, "index coverage",
                f"{len(missing)} of {len(on_disk)} sessions are not indexed "
                f"(newest missing: {newest})",
                "Those sessions cannot be found by search — the files exist,\n"
                "the index does not know about them. Re-encode everything:\n"
                "  reindex(full=True)\n"
                "A plain reindex skips unchanged chunks and will not fix this.")
        return

    rep.add(OK, "index coverage",
            f"{info.points_count} points, {len(indexed)} sessions, none missing")


def check_ollama(rep: Report) -> None:
    try:
        import httpx
    except ImportError:
        rep.add(WARN, "Ollama", "httpx not installed")
        return
    try:
        resp = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=5.0)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception as exc:
        rep.add(FAIL, "Ollama reachable", f"{type(exc).__name__} at {config.OLLAMA_URL}",
                "Start it, then pull the model:\n"
                f"  ollama pull {config.OLLAMA_MODEL}\n"
                "Without it, retrieval degrades to sparse-only.")
        return
    wanted = config.OLLAMA_MODEL
    if not any(m.split(":")[0] == wanted.split(":")[0] for m in models):
        rep.add(FAIL, "embedding model", f"'{wanted}' not present",
                f"  ollama pull {wanted}")
        return
    rep.add(OK, "Ollama", f"{wanted} available")


def check_mcp_registration(rep: Report) -> None:
    candidates = [Path.home() / ".claude.json", Path.cwd() / ".mcp.json"]
    for path in candidates:
        try:
            if path.exists() and "rawthink" in path.read_text(encoding="utf-8"):
                rep.add(OK, "MCP registration", f"found in {path.name}")
                return
        except OSError:
            continue
    rep.add(WARN, "MCP registration", "no rawthink entry found",
            "Register the server:\n"
            "  claude mcp add --scope user rawthink -- rawthink-mcp")


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Diagnose a rawthink installation.")
    ap.add_argument("--vault", default=config.VAULT_PATH)
    args = ap.parse_args(argv)

    vault = Path(args.vault).expanduser().resolve()
    memory_file = Path(os.environ.get("MEMORY_FILE_PATH", vault / "memory.jsonl"))

    # --vault has to move the BM25 path too. config.BM25_STATE_FILE is computed
    # at import time from the default vault, so passing --vault alone produced a
    # half-correct answer: the right vault, checked against the wrong state
    # file. An explicit env var still wins, because that is what the server
    # would use.
    bm25 = (Path(os.environ["RAWTHINK_BM25_STATE"]).expanduser()
            if os.environ.get("RAWTHINK_BM25_STATE")
            else vault / ".bm25_state.json")

    rep = Report()
    check_vault(rep, vault)
    check_graph(rep, memory_file)
    check_bm25_state(rep, bm25, vault)
    client = check_qdrant(rep)
    check_collection(rep, client, vault)
    check_ollama(rep)
    check_mcp_registration(rep)

    if client is not None:
        try:
            client.close()
        except Exception:
            pass

    return rep.render(f"  vault: {vault}\n  graph: {memory_file}")


if __name__ == "__main__":
    raise SystemExit(main())
