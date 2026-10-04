# Document archive

> **HISTORICAL — ARCHIVED DOCUMENTATION. NOT A CURRENT SOURCE.**
>
> Everything in this directory is history. A file here must not be used as a
> source for current capability, architecture, metric or validation claims, and
> it is never checked by the repository consistency guard. When a file here
> disagrees with a current document, the current document wins — see
> [the documentation index](../README.md).

## Why this directory exists

A superseded design is often *correct about the past*: it accurately describes
the topology, the fallback behaviour or the contract that the repository has
since replaced. That accuracy is exactly what makes it dangerous. A plan filed
next to the current guides reads as a peer; a reader who lands on it cannot tell
which one describes the code that is running.

So documentation under `docs/` has exactly two legal states, and no third:

| State | Where it lives | What checks it |
|---|---|---|
| Current / canonical | `docs/` (listed in the [index](../README.md)) | `scripts/check_repo_consistency.py` |
| Historical / archived | `docs/archive/**` | The banner below, enforced by the same guard |

A Markdown file under `docs/` that is neither is *unclassified*, and
`python3 scripts/check_repo_consistency.py` fails. The guard only compares where
a file sits and reads an archived file's opening lines; it never reads claim
wording, so historical sentences inside current documents are unaffected and
`CHANGELOG.md` release history is out of scope.

## Banner contract

Every Markdown file under `docs/archive/` must carry, in its **first 20 lines**,
both of these halves. A marker on its own is not enough — "historical" without
the refusal still reads as a source:

1. an explicit historical / superseded marker (`HISTORICAL`, `archived`,
   `superseded`, `历史`, …), and
2. an explicit refusal naming what the file may not be used as a source
   (current capability, architecture, metric or validation).

Copy this verbatim and replace the final line:

```markdown
> **HISTORICAL — ARCHIVED. SUPERSEDED BY THE CURRENT DOCUMENTATION.**
> This file is kept for lineage only. It must not be used as a source for
> current capability, architecture, metric or validation claims; see
> [the documentation index](../README.md) for the canonical documents.

## <what the document used to claim>
```

The banner has to sit above the content, not in an appendix: a reader has to
meet it before they meet the claims it qualifies.

## Current state of this archive

The archive is empty of moved plans. The former `docs/superpowers/` directory was
removed from the branch rather than relocated, and its files remain retrievable
from this repository's Git history, which is their only archive.