# Branch retention manifest

Produced by the finalization governance pass. It records every branch that
existed at the start of the pass, the SHA it pointed at, its disposition, and a
recovery SHA, so a deleted branch can be reconstructed without a force-push or a
history rewrite.

## Decision rule

A branch was deleted only when **all** of these held:

1. its commits are present in `main` (verified by ancestry where possible, and by
   tree identity where the integration was a squash merge);
2. it has unique, un-integrated work of its own — none;
3. no open PR references it (the PR was merged or closed first);
4. it is not the current work tree.

Branches with unique work, unknown provenance, or an open reference were
retained.

## Before → after

| ref | kind | SHA at start | disposition | recovery |
|---|---|---|---|---|
| `main` | default | `f3d03054d1f108d9ce82abb4499f65a4e0967e46` | **retained** | — |
| `finalize/engineering-closeout` | remote + local | `9546495c282ab971d5e1584868e4b7eb1407c6fd` | **deleted** — integrated (squash #92); auto-deleted by `delete_branch_on_merge` | `refs/pull/92/head`, and `main` tree `1dfd71a` is identical |
| `fix/engineering-audit-p0` | remote + local | `c88b180a79396100a841775650f6de0448ec53c6` | **deleted** — PR #90 closed as superseded; commits contained in `main` via #91→#92 | `refs/pull/90/head` |
| `fix/rerank-gate-reliability-audit` | remote + local | `7d30f3856b13b381d1462d8bfaa9126193193b4a` | **deleted** — PR #91 closed as superseded; commits contained in `main` via #92 | `refs/pull/91/head` |
| `docs/final-governance-closeout` | local (this pass) | branch head | **retained until merged**, then deleted | its PR ref |

Remote heads after cleanup: `main` only. Local `main` matches `origin/main`.

## Integration proof for the deleted branches

The integration was a squash merge, so the deleted heads are not ancestors of
`main`. Equivalence was established by tree identity and ancestry:

```text
#90 head c88b180a  is ancestor of  #91 head 7d30f385   (merge-base --is-ancestor → true)
#91 head 7d30f385  is ancestor of  #92 head 9546495c   (merge-base --is-ancestor → true)
main f3d0305 tree == #92 head 9546495c tree == 1dfd71ad88c2dda259a55fe417801b1efb999b0f
```

Therefore every commit reachable from `c88b180a` and `7d30f385` is represented
in `main`'s tree.

## Not performed

- no force-push;
- no history rewrite;
- no deletion or rewriting of `main` or any tag (`v2.4.0`,
  `safety-pre-merge-dirty`, `archive/*` were all left untouched);
- no deletion of any branch carrying unique or unknown work.
