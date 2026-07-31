---
name: Bug report
about: Something behaves differently than documented
labels: bug
---

## What happened

## What you expected

## `rawthink-doctor` output

Please paste it. Most reports come down to one of its checks — a stale index, a
legacy BM25 state, a service that is not running — and the output usually
identifies which.

```
$ rawthink-doctor --vault <your vault>
```

## Environment

- rawthink-mcp version:
- Python:
- OS:
- Qdrant: Docker / embedded (`QDRANT_PATH`)

## Reproduction

Smallest sequence that shows it. If it involves a vault, synthetic data is
enough where possible:

```bash
python -m tests.generate_synthetic_data --output-dir /tmp/synth
```
