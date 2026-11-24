# Strict real rerank experiment entry points

Refs #18/#125. This adds actual production BiEncoder and CrossEncoder adapters
for explicit benchmark arms (not a measurement). Same RRF per-path candidate
depth is max(100, top_k) for all hybrid arms. CrossEncoder receives up to
max(150, top_k) BiEncoder candidates. Historic hybrid results at other depths
are not directly comparable. Missing local assets or any rerank inference error
blocks the affected query; deterministic fallbacks are not accepted. Default
online behavior and default three-arm CLI selection stay unchanged.

Use the separately approved experiment collection and matching local BGE,
BiEncoder and two CE assets. The rerank publication gate requires byte-level
asset receipts from #144; until that change is merged, rerank output is strictly
diagnostic. Re-run all arms together after both changes land, not by combining
historical reports. No download, model execution or actual ranking quality claim
is made here. ES/Qdrant retrieval errors returning empty hits remain a separate
limitation; independently verify service health and isolate writes.

```bash
python -m benchmarks.retrieval_benchmark --executor real \\
  --dataset /private/reviewed-gold.jsonl \\
  --configs bm25,dense,hybrid_rrf,hybrid_rrf_biencoder,hybrid_rrf_biencoder_crossencoder \\
  --require-integrity --require-source-approval --require-complete-suite --require-publishable
```
