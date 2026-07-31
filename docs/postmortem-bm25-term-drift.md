# Postmortem: silent sparse-retrieval decay

## Status

Resolved in 2.0.0.

## Date

2026-07-31

## Summary

BM25 term IDs were positions in a sorted vocabulary. Every reindex that
introduced a token sorting early shifted the IDs of every token after it, so
sparse vectors written before a reindex no longer matched queries encoded after
it. Nothing failed. RRF kept returning good results off the dense side, so
search felt fine while half the retrieval system was scoring noise.

Measured on a real vault of 1,841 chunks:

| | before | after |
|---|---|---|
| sparse MRR | 0.1596 | **0.6259** |
| sparse nDCG@10 | 0.1851 | **0.6320** |
| sparse hit@10 | 5/19 | **14/19** |
| dense MRR | 0.6842 | 0.6842 |

Dense staying identical is what makes the sparse delta attributable to the fix
rather than to anything else that changed.

## What happened

`BM25Tokenizer.fit()` built its vocabulary as:

```python
all_tokens = sorted(df.keys())
self.vocab = {token: idx for idx, token in enumerate(all_tokens)}
```

The ID of a token therefore depended on which other tokens existed. Adding one
document containing a word that sorts early — `aardvark`, in the reproduction —
shifted everything after it by one.

Reduced to the smallest case that shows it: fit on two documents, then fit again
on the same two plus one that begins with an early-sorting word. Encode the same
query against both. Half the term IDs differ.

Qdrant does not object. A sparse vector with the wrong indices is a valid sparse
vector; it just scores against the wrong terms. The query returns *something*,
and RRF blends it with the dense ranking, where the dense side alone was good
enough to look healthy.

## Why it went unnoticed

Three properties compounded:

**The failure is silent by construction.** A wrong ID is not an invalid ID.
There is no boundary where an error could surface.

**RRF masks a degraded input.** Fusion is designed to be robust to one weak
retriever. That robustness is exactly what hid this: the fused result stayed
useful while one of its two inputs was mostly noise.

**Nobody measured the halves separately.** Overall search quality was fine, so
there was no reason to look. The defect is only visible if you evaluate
sparse-only, which nothing did.

## A second finding

The measurement that confirmed the bug also surfaced something the audit had
not predicted: **40 of 69 sessions in that vault had never been indexed.** The
files were on disk, search could never find them, and nothing said so.

After indexing the six sessions the evaluation queries needed:

| | stale index | after indexing |
|---|---|---|
| dense MRR | 0.6842 | **0.9474** |
| sparse MRR | 0.6259 | **0.8395** |
| sparse hit@10 | 14/19 | **19/19** |

The stale index was costing more than the encoding bug. Both were invisible,
and the two together read as one vague sense that search could be better.

The likely mechanism: `index_vault()` defaulted to `skip_unchanged=True`, and
`reindex(full=True)` also passed `skip_unchanged=True` — so the documented
remedy for a bad index could not fix one. Diagnostics that would have named the
skipped files were printed to stdout, which is the JSON-RPC channel, so they
never arrived anywhere.

## Fixes

**Term IDs are a hash of the token** (`blake2b`, 31 bits), so an ID depends on
nothing but the token. `format_version` is 2; a state file from the old scheme
is rejected with an error rather than loaded, and `initialize()` deletes it with
a warning.

**`index_vault(force=True)`** re-encodes every chunk, and `reindex(full=True)`
now passes it.

**Diagnostics go to stderr**, so the record of what was skipped survives.

**`rawthink-doctor`** compares vault files against indexed sessions and reports
the gap with the command that closes it.

**Regression locks** in `tests/unit/test_sparse.py`: one asserts the invariant
(same token, same ID, different corpora), one asserts the mechanism (vocab
values are not positions). The second exists because the first can be satisfied
by an implementation that reintroduces the bug differently.

## What to take from it

**A warning that lets the write through is a decision to allow it.** The same
codebase accepted non-canonical relation types with a warning, and accumulated
56 of them. Neither warning was acted on, because nothing was watching.

**Robustness can hide the thing it protects against.** RRF did its job. Its job
included making a broken retriever invisible.

**Measure the halves.** Aggregate quality was acceptable throughout. The defect
was only ever visible by evaluating each retriever alone — which took an hour,
after months of not knowing.

**Reproducibility of the measurement mattered more than the fix.** The baseline
could not have been produced after the fix. Taking it first is what made the
delta a number rather than a claim.
