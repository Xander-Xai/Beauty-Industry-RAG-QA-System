# Repository Truth Audit

Audit base: `7b03267ccd751178e5e1d69ec6a6ec97281b57cb` (`origin/main`, fetched 2026-10-01).
Canonical runtime version: `config.json` → `system.version` (`2.3.0`); release history is recorded in `CHANGELOG.md`. The repository has no GitHub Release at audit time.

Status values: `VERIFIED`, `PARTIAL`, `PLANNED`, `BROKEN`, `STALE`, `HISTORICAL`.

| Area | Claim | Source | Code Evidence | Status | Action |
|---|---|---|---|---|---|
| Application | FastAPI monolith is the canonical app | README, deployment guide | `app.py`, `api/routes.py`, `api/routes_auth.py` | VERIFIED | Document `/api/*` as default path |
| Microservices | Service directories exist, production/frontend integration is not established here | README, compose files | `api-gateway/`, `retrieval-service/`, `generation-service/`, `monitoring-service/`; separate entrypoints | PARTIAL | Keep as secondary code; require independent contract and deployment validation |
| Offline ingestion | Document parsing/OCR/embeddings/writers/scheduling/feedback are available | Prior README, guides, PRD | `offline/` has only `finetune_qlora.py`, `finetune_data.json`, `requirements-finetune.txt` | PLANNED | Correct claims; track implementation in Issue #2 |
| Offline entrypoint | `run_offline.py` supports ingestion modes | Prior README and guides | `run_offline.py` rejects ingestion modes; `rewrite/feedback.py` implements rewrite feedback separately | PARTIAL | Keep explicit fail-fast; do not advertise ingestion commands |
| Offline tests | `tests/test_offline_pipeline.py` is a regression suite | Test filename/content | Moved to `tests/contracts/offline_pipeline_contract.py`; production modules it imports are absent | HISTORICAL | Retain as non-collected contract until implementation exists |
| QLoRA | Fine-tuning utility exists; a trained adapter and successful training run are not included | README, PRD | `offline/finetune_qlora.py`, `offline/finetune_data.json`, `offline/requirements-finetune.txt` | PARTIAL | Describe utility only; do not imply trained weights |
| AdapterManager | PEFT adapter lifecycle code is integrated with LLM client | README, PRD | `models/adapter_manager.py`, `models/llm_client.py` | PARTIAL | Runtime use depends on optional PEFT/model assets and configuration |
| RRF | Weighted fusion code is present in retrieval path | README, PRD | `retrieval/parallel_recall.py`, `retrieval-service/rerank/rrf_fusion.py` | VERIFIED | Capability implementation; production quality is not inferred |
| BiEncoder | BiEncoder reranker is called from online pipeline | README, PRD | `retrieval/bi_encoder.py`, `core/pipeline.py` | PARTIAL | Requires model configuration/assets and runtime validation |
| RAGAS | Evaluation harness and data exist | README, PRD | `tests/evaluation/ragas_eval.py`, `tests/evaluation/` | PARTIAL | Evaluation tooling is not a quality score or production validation |
| Airflow | A DAG draft describes scheduled ingestion | PRD, `dags/knowledge_base_dags.py` | Draft references missing `offline.scheduler`/`offline.feedback_loop`; DAGs are guarded and not registered without them | PLANNED | Keep as plan; no end-to-end scheduled ingestion pipeline is available |
| Configuration | Runtime configuration uses `config.json` plus environment loading | README, deployment guide | `config.json`, `common/config.py`, `.env.example` | VERIFIED | Keep secrets in environment; runtime version is canonical here |
| Runtime version | Runtime version agrees with latest formal changelog entry | config and changelog | `config.json:system.version`; `CHANGELOG.md:[2.3.0]` | VERIFIED | Guard against drift; Unreleased does not bump runtime version |
| License | Project declares MIT | README | Root `LICENSE` added as standard MIT text | VERIFIED | README links to license |
| CI | Python test workflow runs on PR/push to main | CI workflow | `.github/workflows/ci.yml` | PARTIAL | Add collection, consistency and compile checks; report actual results |
| Ruff | Ruff lint and format checks are configured | lint workflow | `.github/workflows/lint.yml` | PARTIAL | Preserve checks and fix reported issues |
| Security | pip-audit and secret scanning are configured | security workflow | `.github/workflows/security.yml`; TruffleHog action previously used floating `@main` | PARTIAL | Keep both as merge checks; pin action to stable release |
| Docs and plans | Current guides and historical plans have different roles | docs index | `docs/README.md`, `docs/superpowers/{plans,specs}/` | VERIFIED | Keep plans intact; current status belongs in canonical docs |
| Working artifacts | PID/stopped markers are tool runtime state | tracked hidden files | `.superpowers/**/state/server.pid`, `server-stopped` | STALE | Remove these markers and ignore matching runtime files |
| Repository consistency | File links, version and active entrypoint should be checked automatically | CI | `scripts/check_repo_consistency.py` | VERIFIED | Run from CI and locally |

## Offline history check

At the audit base, `find offline -maxdepth 2 -type f` lists only the three QLoRA files above. `git log --all -- offline/document_processor.py`, `offline/image_processor.py`, `offline/vectorizer.py`, `offline/scheduler.py`, and `offline/feedback_loop.py` contains no implementation commits. Older changelog statements naming these modules are retained as historical text and corrected below rather than silently rewritten.

`tests/contracts/offline_pipeline_contract.py` and `tests/contracts/archive_expired_contract.py` are deliberately not named `test_*.py`, so pytest does not collect imports for code that is not present. They preserve proposed acceptance behavior; they are not regression tests or implementation evidence.

## Version policy

`config.json` `system.version` is the canonical runtime version. The newest dated version in `CHANGELOG.md` must match it. `Unreleased` records changes after that release without assigning a new runtime version. Git tags/releases are separate publication decisions; no GitHub Release exists at this audit base.
