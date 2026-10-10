# Finalization closeout

The closeout deliverables for the `finalize/engineering-closeout` branch
(base: PR #91, `fix/rerank-gate-reliability-audit`).

| document | purpose |
|---|---|
| [FINAL_ENGINEERING_AUDIT.md](FINAL_ENGINEERING_AUDIT.md) | defects found and fixed, what was deliberately left, residual risks |
| [ACCEPTANCE_MATRIX.md](ACCEPTANCE_MATRIX.md) | every check → evidence path → command → result → verdict |
| [REPRODUCIBLE_EVALUATION.md](REPRODUCIBLE_EVALUATION.md) | data/module prep, annotation spec, commands, artifact formats |
| [EVIDENCE_MANIFEST.md](EVIDENCE_MANIFEST.md) | capability → source → test → CI → artifact → level |
| [INTERVIEW_GUIDE.md](INTERVIEW_GUIDE.md) | 30 s / 90 s intros, architecture, 5 follow-ups, 3 failure cases |
| [RESUME_CLAIMS.md](RESUME_CLAIMS.md) | what may and may not be claimed |

Boundary that applies to every file here: this is **post-employment open-source
work**, not an employer's production system, and no retrieval metric is claimed
because no corpus or CrossEncoder weights are present. The host used for local
verification has a single GPU (1× RTX 5060 Ti), not a dual-A5000 topology.

Commit at time of writing: `61be8a9`.
