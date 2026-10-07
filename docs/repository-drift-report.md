# Repository drift report

> **Scope.** A point-in-time truth audit of the current documentation against the
> code, `config.json` and the live GitHub state, run after the `#58` / `#59` /
> `#61` merge wave. It records what was found and what was changed. It is an audit
> record, not a capability source, and it promotes no evidence.
>
> **Method.** Every claim was read against the code, `config.json`, the test tree
> or `scripts/check_repo_consistency.py`. The mechanical guard already covers
> link/anchor integrity, evidence vocabulary, retired topology, Python entrypoints
> and derived counts; this report only lists what that guard does **not** cover.
> Severity follows the repository's own scheme: **P0** factual falsehood,
> **P1** reader-visible contradiction, **P2** stale operational/documentation
> issue, **P3** cosmetic.

## Findings

| ID | Severity | Category | File A | File B / code source | Conflict | Canonical truth | Required action | Evidence | Status |
|---|---|---|---|---|---|---|---|---|---|
| DRIFT-001 | **P1** | Security claim drift | `SECURITY.md:79` — "There is also no ingestion-side content inspection, sanitization, or quarantine." | `offline/source_trust.py`, seal gate `offline/validator.py:152`, `common/audit.py:400-401` | A public security document denied that any ingestion quarantine exists, one merge after a provenance-and-quarantine contract (`#61`) was added. | A bounded **provenance** quarantine gate exists; it reads no document **content**, so the "no content inspection/sanitization" half was true and the "or quarantine" half was false. | Reword to state the provenance gate exists and does not inspect content. | `docs/security-regression-coverage.md` rows B and H | **RESOLVED** |
| DRIFT-002 | P2 | Evidence-level wording | `docs/deployment-guide-k8s.md:27` — "全部对运行中的应用验证过" covering `/api/ready` | `tests/test_readiness_endpoint.py` (in-process `TestClient`), `docs/evidence-map.md:27`, `docs/repository-truth-audit.md:96` | `/api/ready` 200/503 was described as verified against a running application, but it is contract-tested in-process; real cluster admission is `PENDING`. | `/api/health` and `/api/metrics` were runtime-observed; `/api/ready` is `REPO_VERIFIED` (deterministic contract) only. | State the contract-test scope and that real cluster admission is `PENDING`. | `tests/test_readiness_endpoint.py:89` | **RESOLVED** |
| DRIFT-003 | P2 | Stale reference | `docs/slo-runbook.md:307` — `$VLLM_14B_URL` | `.env.example:54`, `router/stateless_router.py:116`, `deploy/k8s/configmap.yaml:33` | The runbook used an environment variable that is never defined; the command silently expands to `/v1/models`. | The variable is `VLLM_GEN_14B_URL`. | Rename in the runbook. | `.env.example:54` | **RESOLVED** |
| DRIFT-004 | P2 | Index self-contradiction | `docs/README.md:118-120` claims every current doc is listed in the index | `docs/main-branch-governance.md` (current, in the guard's canonical set) | A current document was not listed in the index that claims to list every current document. | `main-branch-governance.md` is canonical and belongs in the index. | Add it to the index. | `scripts/check_repo_consistency.py` canonical-doc set | **RESOLVED** |
| DRIFT-005 | P2 | Volatile exact count | `docs/security-regression-coverage.md:82-94` — "1 failed, 140 passed" … "13 failed, 212 passed" | same file, row totals (141 vs 225) | The mutation table carried pass counts from different points in the PR's development, so its own totals disagree and drift as the suite grows. | The failure count is the meaningful mutation outcome; the pass count only restates the suite size at one moment. | Drop the pass counts and state why. | `pytest --collect-only tests/offline/` | **RESOLVED** |
| DRIFT-006 | P3 | Stale exact count | `CHANGELOG.md:52` — "Twelve mutation checks" | `docs/security-regression-coverage.md` (`#61` added 13 rows) | Off-by-one in a changelog count. | `#61` added thirteen mutation-check rows. | Correct the number. | `git show f99a55f -- docs/security-regression-coverage.md` | **RESOLVED** |
| DRIFT-007 | P3 | Stale exact count | `docs/technical-walkthrough.md:3` — "30,000+ 字 README" | `README.md` (26,595 characters) | The walkthrough overstated the README length. | The README is ~26.6k characters. | Correct the number. | `wc -m README.md` | **RESOLVED** — the document it referred to was later removed from this repository; the finding is kept as history |
| DRIFT-008 | P3 | Command inconsistency | `docs/deployment-guide.md:58` — `npm install` | `README.md:233`, `.github/workflows/ci.yml`, `docs/open-source-hardcoding-audit.md` | One guide prescribed `npm install` while every other current doc and CI use the lockfile-strict `npm ci`. | The canonical reproducible build is `npm ci`. | Use `npm ci`. | `frontend/package-lock.json` | **RESOLVED** |

## Deliberately not changed

| Item | Severity | Why |
|---|---|---|
| `AUDIT_REPORT.md` (repo root) | P3 | It is **not tracked**; `.gitignore:94` excludes it. It is a local-only historical artifact and is not in the public repository, so it is out of the repository-truth scope. It must not be committed. |
| `PRD.md` design/target sections | none | The PRD is explicitly labelled design-stage, and its reconciliation table separates current implementation from historical/target design; the consistency guard and the docs index both treat it as a design document, not a current-state source. |
| `PRD.md:213` "线上验证" (weights from online validation) | P2 | It describes the former production system's tuning, consistent with the `HISTORICAL_PRODUCTION` framing used elsewhere; it is not a claim about this repository. Recorded here rather than rewritten to avoid inventing a provenance for the number. |
| `CHANGELOG.md:644` names a removed test path | none | Inside a retained historical release entry that the truth audit already declares non-evidence. |

## Outcome

- **P0 conflicts: 0.** **P1 conflicts: 0** after DRIFT-001.
- The canonical documents — `README.md`, `docs/architecture-baseline.md`,
  `docs/evidence-map.md`,
  `docs/repository-metadata.md` — were checked against each other and agree on
  architecture (FastAPI monolith canonical; microservices/K8s `REPO_VERIFIED`
  components / `PENDING` deployment), model topology (single shared 4B endpoint +
  14B), version (`2.3.0`) and evidence boundaries.
- No evidence level was promoted by any fix: `EVIDENCE_LEVEL_CHANGES = NONE`.
