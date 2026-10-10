# GitHub governance report — integration and closeout

The record of how the open PR chain, the open issues, and the remote branches
were reconciled into a single maintainable default branch. Every state below was
re-derived live from the GitHub API and `git` in this pass, not copied from an
earlier snapshot.

- Repository: `Xander-Xai/Beauty-Industry-RAG-QA-System` (public, default `main`)
- Branch protection on `main`: strict required status checks (9 contexts),
  `enforce_admins` on, no force-push, no branch deletion, conversation
  resolution required, 0 required approvals.
- Merge strategy used for the integration: **squash** (the same shape as the
  preceding `#88`/`#89` releases, each of which landed as one commit on `main`).

## 1. Integration PR chain

Verified ancestry before the merge: `#90 HEAD` is a strict ancestor of `#91
HEAD`, which is a strict ancestor of `#92 HEAD` (`git merge-base --is-ancestor`
→ true for both). `#92` was therefore the single complete integration candidate.

| PR | topic | base | head SHA | CI at head | review threads | diff vs `main` | disposition |
|---|---|---|---|---|---|---|---|
| #90 | P0 audit fixes (evidence self-consistency, publication gate, doc truth) | `main` | `c88b180a` | 9 pass / 1 skip | none | 9 commits, strict subset of #91 | **CLOSED — superseded** (not merged) |
| #91 | rerank/gate/routing degradation observability | `main` | `7d30f385` | 9 pass / 1 skip | none | 13 commits, strict subset of #92 | **CLOSED — superseded** (not merged) |
| #92 | closeout: rerank/gate/session false-claim defects + closeout set | `main` | `9546495c` | 9 pass / 1 skip | none | 19 commits, full union | **MERGED — squash** → `f3d0305` |

No PR carried an unresolved review thread and none carried a review decision;
`main` requires no approving review, so the only gates were the required status
checks and conversation resolution, both satisfied at the `#92` head.

## 2. Merge-equivalence proof (squash)

Because the integration was a squash, the individual commit SHAs are not
ancestors of `main`. Equivalence was proven by tree identity instead:

```text
git rev-parse f3d0305^{tree}   → 1dfd71ad88c2dda259a55fe417801b1efb999b0f
git rev-parse 9546495c^{tree}  → 1dfd71ad88c2dda259a55fe417801b1efb999b0f
```

The trees are byte-identical, and `#90`/`#91` heads are strict ancestors of
`9546495c`, so every change from both superseded branches is present in `main`.
`#90` and `#91` were closed as superseded with an explanatory comment and the
integration link; neither was recorded as individually merged.

## 3. Issue decisions

| issue | topic | disposition | reason |
|---|---|---|---|
| #84 | code/doc drift (5 sites) | **CLOSED — completed** | all 5 sites fixed or re-worded to match code; contract test + single-fusion regression + guard pass |
| #85 | production validation checklist completeness | **CLOSED — completed** | 5 entries registered; `VAL-DEGRADE-001` executed and promoted and `VAL-STORE-001` executed with a committed artifact; other entries deliberately remain `NOT EXECUTED` |
| #86 | golden-set identity and label gaps | **OPEN** | the attribution framework is implemented and tested, but the 301-row human annotation pass and stable `doc_id::chunk_id` identity still do not exist |
| #8 | external validation index (umbrella) | **OPEN — deferred** | 4 real-model smokes still `PENDING` |
| #12 | real RAGAS + 4B/14B vLLM topology | **OPEN — deferred** | no evaluator report, no GPU topology record |
| #18 | real retrieval benchmark execution | **OPEN — deferred** | all configs `BLOCKED`; no committed artifact |
| #32 | performance artifact + observability runtime | **OPEN — deferred** | no performance/OTLP/Grafana/alert runtime record |
| #54 | Kubernetes deployment/readiness smoke | **OPEN — deferred** | no real cluster available |
| #60 | browser-to-real-RAG end-to-end smoke | **OPEN — deferred** | no Playwright run against the real backend |

Closing #84/#85 records *engineering* completion only. It is not evidence that
any external runtime validation passed. The six deferred issues have **no**
qualifying artifact, so they stay open; a green CI run, a code freeze, or a
decision to stop prioritising them would not be a reason to close them.

## 4. Branches

See [`BRANCH_RETENTION_MANIFEST.md`](../../BRANCH_RETENTION_MANIFEST.md) for the
retained/deleted set and the recovery SHAs.

## 5. Final default-branch state

| field | value |
|---|---|
| `main` after integration | `f3d03054d1f108d9ce82abb4499f65a4e0967e46` |
| tree | `1dfd71ad88c2dda259a55fe417801b1efb999b0f` |
| required checks | Ruff, Dockerfile, 前端构建, 企业就绪配置, 测试套件 (3.10/3.11), 评估确定性守卫, pip-audit, 敏感信息扫描 |
| local suite at the integration head | `2896 passed, 17 skipped` |
| retrieval metrics | `NOT_MEASURABLE` (all configs `BLOCKED`) — no value claimed |

## 6. Actions that would require explicit approval (not performed)

- force-push, history rewrite, or tag rewrite on any shared ref;
- deleting `main`;
- obtaining real model weights, GPU topology, evaluator credentials, or an
  external business dataset;
- promoting any external validation issue to done without a qualifying artifact.

## 7. Resume-usable outcomes

The verified, non-metric outcomes of this pass are the *defects found and fixed*
and the *governance work*: the rerank-evidence false-positive class, the
`.mean()` collapse that made a comparison structurally constant, the
cross-principal session leak, the overload status-contract defect, the
golden-set data contract and human-review lifecycle, and this integration /
supersession / branch-retention reconciliation. No retrieval-quality number is
claimed. See [`RESUME_CLAIMS.md`](RESUME_CLAIMS.md).
