# Approved Dense experiment builder — 2026-11-24

Refs #18/#125. A new isolated vector collection can now be prepared from a
configured, approved ES experiment index and its exact chunk JSONL. No
production index or collection is written; no aliases or active epochs
are changed. Default invocation is read-only. Use a dedicated experiment
config, new experiment version and fresh index/collection names, not the
frozen historical BM25 artifacts. Obtain human approval using the existing
source ledger process; this command does not grant approval.

Set ES and Qdrant hosts to loopback, ES index to
`cosmetics_docs_exp_<new_experiment>`, text collection to
`rag_text_exp_<new_experiment>`, and model_path to an existing reviewed local
BGE model directory. Configure the matching experiment epoch and source
approval ledger. The chunk file must correspond exactly to the approved ES
population, with stable doc/chunk identities and original content hashes.

```bash
CONFIG_PATH=/path/to/experiment.json python -m scripts.corpus.index_approved_dense_experiment \
  --chunks /path/to/experiment/chunks.jsonl
# Only after reviewing READY and confirming the fresh collection:
CONFIG_PATH=/path/to/experiment.json python -m scripts.corpus.index_approved_dense_experiment \
  --chunks /path/to/experiment/chunks.jsonl --write \
  --confirm-collection rag_text_exp_new_experiment
```

Uses the production BGETextEmbedder and QdrantTextWriter upsert on a newly
created collection; validates ES/Qdrant payload parity after write. No
automatic cleanup or retry into an existing collection: a failed build
may leave a partial experimental collection. Use an independent quiescent
local environment. Model directory completeness is not a weight signature
or correctness proof; supply reviewed assets, test actual vectors and
record model revision separately. A BUILT receipt is not a retrieval metric.
Run the existing benchmark CLI with all publication gates afterward.
No live model or Qdrant execution is claimed by this implementation.

Before creating the fresh collection, validate the full returned embedding
population, dimensions and finite nonzero cosine norms. Degenerate all-zero
vectors cannot produce a BUILT receipt through this builder. This check does
not attest model identity or independently verify persisted vector values.

## Local model asset binding

The builder hashes the actual model directory bytes (including tokenizer and
configuration files), records the content digest in every vector's embedding
version, and checks that identity again after embedding. Dense parity requires
all vector payloads to match the current local model directory identity before
and after the retrieval run. Old unbound experiment collections must be rebuilt
under a fresh name. This is a reproducibility check, not a signature or proof of
what a process loaded: use quiescent, reviewed local assets; a change-and-restore
race is not excluded. Hashing large weight files adds setup/audit time, not query
latency. No actual model retrieval has been run by this change.
