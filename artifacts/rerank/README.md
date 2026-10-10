# Rerank validation artifacts

Runtime output of `python3 -m retrieval.rerank_validation` lands here.

These files are **generated, never committed**. Each is written on the host that
actually has the CrossEncoder weights, and its `git_sha` names the commit that
produced it.

## What a file contains

| field | meaning |
|---|---|
| `status` | `OK` (real inference ran) · `PENDING` (weights absent) · `FAILED` (load error) |
| `provenance` | `cross_encoder` · `deterministic_fallback` |
| `is_reranking_evidence` | `true` only when **both** `status=OK` and `provenance=cross_encoder` |
| `git_sha` / `git_dirty` | the tree this was produced from |
| `hardware` | `cuda_available` and the visible GPUs; a CPU run records `gpus: []` |
| `model_revision` | content fingerprint of the loaded weights, or `null` |
| `degradations` | stage-by-stage record of what fell back and why |
| `comparison` | real vs deterministic-fallback ordering, when both sides ran |

## Reading one safely

The only field to check before quoting anything is `is_reranking_evidence`.

A report with `provenance: deterministic_fallback` was produced **without** the
second-stage reranker. Its ranking is BiEncoder order with
`ce_score_ensemble = 0`. That is the correct degraded behaviour, but it is **not
a reranking result**, and no "CrossEncoder improved X" statement may be derived
from such a file. `top1_rate_delta` is omitted entirely in that case rather than
reported as 0.0 — a fallback-vs-fallback comparison measures nothing.

## Generating

```bash
# status only; does not load a model
python3 -m retrieval.rerank_validation --smoke

# real vs fallback comparison
python3 -m retrieval.rerank_validation --compare

# fail CI unless real CrossEncoder inference produced the result
python3 -m retrieval.rerank_validation --smoke --require-real
```

Exit codes: `0` ok · `1` failed · `2` configuration error · `3` pending (an
absent asset, not a failure).

This harness never downloads weights. Loading is attempted only from the
configured local paths.