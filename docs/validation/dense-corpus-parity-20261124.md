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
identities and returned vector values rounded to six decimal places (versioned
`vector-snapshot/unit-decimal6/v2` digest). The second CI failure exposed a
raw-input persistence discrepancy: [0.6, 0.8] before reopen versus [3.0, 4.0]
after reopen (reconstructed from the actual failing SHA256 values). The builder
now writes unit-normalized vectors; the reader rejects non-unit stored norms
(absolute tolerance 1e-5) rather than hiding storage drift by normalization.
Sub-precision changes are not guaranteed detectable;
rounding boundaries may still conservatively block a run. Any change in that fingerprint between
pre/post checks blocks publication. This is read-only and bounded by the
existing 10,000-point limit. It does not prove the original embedding model
or prevent transient changes between observations; independently reviewed
model provenance and a quiescent experiment are still required.


The publication gate rejects legacy payload-only receipts even if both
match. A disk-backed Qdrant local-engine roundtrip regression creates a
synthetic cosine collection with unit inputs, closes/reopens it, and updates only the vector.
Payload equality remains unchanged but the publication gate must reject
the changed vector fingerprint. This is actual local storage API exercise,
not a live Qdrant server or a BGE retrieval-quality experiment.
