# Tests

Two kinds of thing live here, and they are not interchangeable.

## `tests/unit/` — runs anywhere

```bash
pytest
```

101 tests, no Docker and no Ollama. They run in CI on every push across Python
3.10–3.13.

This is possible because the external dependencies sit at the edges: seven of
nine modules import neither `qdrant_client` nor `ollama`, Qdrant runs in-process
via `:memory:`, and `Indexer` takes an injectable embedder so the whole indexing
path can be exercised with a deterministic stub.

The stub carries no semantic similarity. It can verify that a chunk was indexed,
deduped or paginated. It cannot verify that retrieval is *good* — that question
belongs to the harness below.

Two markers are excluded by default in `pyproject.toml`:

- `integration` — needs a real Qdrant and/or Ollama
- `measurement` — reports numbers rather than asserting, needs a real vault

They are opt-in rather than auto-skipped on a missing service. A suite that
silently skips reports green for a run that checked nothing.

```bash
RAWTHINK_TEST_QDRANT=http://localhost:6333 \
RAWTHINK_TEST_OLLAMA=http://localhost:11434 \
pytest -m integration
```

## The scripts — run by hand

`search_quality.py`, `search_overlap_analysis.py`, `benchmark_scale.py` and
`generate_synthetic_data.py` are measurement tools, not tests. They need a
running Qdrant and Ollama, they take minutes, and they print reports instead of
asserting. CI does not run them.

```bash
python -m tests.generate_synthetic_data --output-dir /tmp/synth
python -m tests.search_quality
```

`search_quality.py` loads its evaluation set from `RAWTHINK_EVAL_GT`, defaulting
to `ground_truth.example.json` — a synthetic set whose entity names match what
the generator produces, so it runs on any machine.

**Point it at your own set for a number that means anything for your data**, and
keep that file outside the repo:

```bash
RAWTHINK_EVAL_GT=~/rawthink-private/ground_truth.json python -m tests.search_quality
```

The evaluation set used to be hardcoded in `search_quality.py`. That meant the
harness only ran on one person's vault, and personal entity names shipped inside
the package. `tests/ground_truth.local.json` is gitignored for the same reason.

## Adding a regression test

When you fix something, write the test that fails without the fix — then check
that it does, by reverting the fix and watching it go red. A test that passes
both ways is worse than no test: it reports safety that is not there.

The docstrings here describe the defect, not the assertion. Six months from now
the useful question is "why does this exist", and the answer should be in the
file.
