# Dense / Hybrid corpus parity — 2026-11-24

The existing approved BM25 evidence remains frozen. For future dense/hybrid
runs, the CLI additionally reads the configured ES index and Qdrant collection
before and after retrieval. A source approval on ES alone cannot authorize
vector results. Exact counts, unique (doc_id, chunk_id), epoch, full content,
role/dept masks, status and provenance must match; the ES identity digest must
match its approval receipt. Missing stores, incomplete scans, extra points,
metadata-only markers, mismatches and changed receipts block publication.
Use a dedicated quiescent experiment collection without lifecycle markers;
the audit is intentionally bounded to 10,000 documents, not a production
migration utility. BM25-only execution is unchanged.

Use the existing real benchmark CLI with `--require-publishable` in addition
to the contract, integrity, results, source-approval and complete-suite gates.
No new model weights, source approvals or index writes were performed here.
Matching pre/post payloads do not prove immutable snapshots during retrieval,
correct embedding weights or vector values; these remain independent gates
for an operator-reviewed experiment. No dense/hybrid metrics are claimed.


## Persisted vector receipt

The actual dense experiment builder and pre/post benchmark audit now request
unnamed stored vectors from Qdrant, validate the configured dimension and
finite nonzero cosine norm, and emit a stable SHA256 over point IDs, chunk
identities and returned vector values. Any change in that fingerprint between
pre/post checks blocks publication. This is read-only and bounded by the
existing 10,000-point limit. It does not prove the original embedding model
or prevent transient changes between observations; independently reviewed
model provenance and a quiescent experiment are still required.
