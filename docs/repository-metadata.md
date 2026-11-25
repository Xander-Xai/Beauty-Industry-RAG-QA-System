# GitHub repository metadata

This record reflects a live GitHub API read on **2026-11-25**. Re-read the repository before treating these values as current; this document does not update GitHub automatically.

## Live values

Repository: [Xander-Xai/Beauty-Industry-RAG-QA-System](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System)

| Field | Live value at review | Policy |
|---|---|---|
| Description | `Multimodal RAG for cosmetics knowledge QA with hybrid retrieval, evidence-grounded answers, RBAC and reproducible evaluation.` | Describes implemented scope without claiming production acceptance. |
| Topics | `cosmetics`, `docker`, `document-processing`, `elasticsearch`, `evidence-gating`, `fastapi`, `hybrid-search`, `information-retrieval`, `llm`, `llm-evaluation`, `multimodal-rag`, `python`, `qdrant`, `rag`, `rbac`, `redis`, `reranking`, `retrieval-augmented-generation` | 18 accurate discovery tags. Kubernetes and observability deployment implications were removed; no Agent framework or unused vector store is listed. |
| Homepage | Empty | Keep empty: there is no public app or documentation site. |
| Default branch | `main` | Protected; required status checks remain enabled. |
| Delete branch on merge | Enabled | Keep enabled for short-lived PR branches. |
| Merge methods | Merge commit, squash and rebase enabled | Existing policy; not changed in this pass. |
| Security | Vulnerability alerts, Dependabot security updates, secret scanning, push protection and private vulnerability reporting enabled | Existing scanning and branch checks remain enabled. No security response SLA is claimed. |
| Social preview | Not verifiable through the repository metadata API | GitHub's public repository API does not expose the uploaded social-preview image. No claim is made that one is set; Settings UI review is required. |

The repository is public, Issues are enabled, and the GitHub security policy is published. No website is configured. The main branch is protected with eight required status checks. Merge methods and automatic branch deletion were not changed.

The Kubernetes manifests have 31 static checks in `tests/deploy/test_k8s_manifests.py`; these are static checks only, and no real cluster deployment is verified, so the `kubernetes` topic was removed.

## Read-back commands

```bash
gh repo view Xander-Xai/Beauty-Industry-RAG-QA-System \
  --json description,homepageUrl,defaultBranchRef,repositoryTopics
gh api repos/Xander-Xai/Beauty-Industry-RAG-QA-System \
  --jq '{has_issues,delete_branch_on_merge,allow_merge_commit,allow_squash_merge,allow_rebase_merge,security_and_analysis}'
gh api repos/Xander-Xai/Beauty-Industry-RAG-QA-System/branches/main/protection
gh api repos/Xander-Xai/Beauty-Industry-RAG-QA-System/topics
```

Keep this record and the live repository settings aligned after an authorized change. Never point `homepage` at a local, private or nonexistent service, and never use description/topics to imply real model, GPU, cluster or production validation.
