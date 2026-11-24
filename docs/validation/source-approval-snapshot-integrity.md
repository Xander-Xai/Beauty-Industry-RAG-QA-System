# Source approval snapshot integrity (#18, #125, #154)

The existing source approval audit now rejects conflicting provenance on chunks
sharing a source, duplicate chunk IDs, missing document identity/epoch, repeated
pagination cursors and count drift. The historical identity-only
`content_sha256` is unchanged for compatibility. The additional
`indexed_payload_sha256` binds observed IDs, epochs, text, role/department
masks, status and persisted provenance without exporting raw indexed text.

A run with a verified preflight source receipt and measured retrieval rechecks
approval and observed payload after execution, including BM25-only runs. Missing
or changed receipts block publication and strict source-approval acceptance.
The comparison is observational, not transactional isolation: an operator must
freeze the isolated experiment corpus and approvals for the full run. Identical
receipts do not establish faithful source-to-chunk ingestion, an absence of
transient mutation, or current regulatory correctness. Real backend, reviewer
and model evidence remain separate. Tests use controlled fixtures; the existing
27-case real BM25 artifact is not rewritten or upgraded.
