#!/usr/bin/env python3
"""Recompute a published benchmark result from its committed evidence.

The evidence in `docs/validation/evidence/` is deliberately small: it holds the
per-sample ranks the metrics were computed from, the environment and index
identity, and the artifact digests — but not the raw run artifacts, which are
local runtime output by the repository's own rule
(`artifacts/benchmarks/README.md`). Publishing "0.7037 plus a SHA-256 of a file
nobody can fetch" is not reproducibility; it is a number with a footnote.

This script closes that gap. It re-derives every reported metric **from the
committed per-sample rows alone**, using the repository's own metric
implementation, and compares the result to the published manifest. It needs no
Elasticsearch, no model weights and no network, so a third party can check the
claim rather than trust it.

    python3 scripts/validation/verify_benchmark_evidence.py
    python3 scripts/validation/verify_benchmark_evidence.py --run ab_smartcn --verbose

Exit code 0 means every published metric was reproduced from the committed
evidence. Exit 1 means at least one did not, and the script says which.

What this does **not** do: it cannot re-execute the retrieval. Re-running needs
the approved index, which needs the approval ledger, which is not committed.
What it does do is prove the arithmetic and the per-sample data are consistent
with the headline numbers — which is the part a reader cannot otherwise check.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EVIDENCE_DIR = REPO_ROOT / "docs" / "validation" / "evidence"
MANIFEST_PATH = EVIDENCE_DIR / "real-v1-approved-bm25-evidence.json"
PER_SAMPLE_PATH = EVIDENCE_DIR / "real-v1-approved-bm25-per-sample.jsonl"

#: Metrics this script re-derives, and the cutoffs they are defined at.
CUTOFFS = (1, 3, 5, 10)


def _display(path: Path) -> str:
    """Show a path relative to the repository when it is inside it.

    Falls back to the absolute path rather than raising: the tool is also used
    against evidence copied elsewhere, and refusing to verify a file for being
    in an unexpected directory would be the wrong kind of strictness.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_manifest(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"evidence manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_per_sample(path: Path) -> dict[str, dict]:
    if not path.is_file():
        raise SystemExit(f"per-sample evidence not found: {path}")
    rows = {}
    # Read `path`, not the module constant. Validating one file and reading
    # another is how `--per-sample` becomes a flag that is accepted, printed and
    # then silently ignored — which would make this tool verify the committed
    # evidence no matter what it was pointed at.
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[row["sample_id"]] = row
    return rows


def recompute(per_sample: dict[str, dict], run_name: str) -> dict[str, float]:
    """Re-derive the published metrics from committed per-sample rows.

    Goes through the repository's own :func:`benchmarks.metrics.score_ranking`
    and :func:`benchmarks.metrics.aggregate_results`, so this is a genuine
    cross-check of the recorded artifact rather than a restatement of the same
    arithmetic. Raises when a row is missing the run, because a summary that
    silently ignored absent rows would understate the denominator — the exact
    failure mode the metrics exist to avoid.
    """
    from benchmarks.metrics import aggregate_results, score_ranking
    from benchmarks.models import QueryBenchmarkResult

    missing = [sid for sid, row in per_sample.items() if run_name not in row]
    if missing:
        raise SystemExit(f"per-sample evidence has no '{run_name}' entry for: {sorted(missing)}")

    results = []
    for sid in sorted(per_sample):
        row = per_sample[sid]
        arm = row[run_name]
        # The evidence carries chunk ids, not the composite doc::chunk keys the
        # benchmark uses. Both sides are built the same way here, so the
        # comparison is exact: a gold chunk id is present in a retrieved id list
        # iff the corresponding key would have matched.
        gold = [f"doc::{chunk}" for chunk in row["gold_chunk_ids"]]
        retrieved = [f"doc::{chunk}" for chunk in arm["retrieved_chunk_ids"]]
        scored = score_ranking(gold, retrieved)
        results.append(
            QueryBenchmarkResult(
                sample_id=sid,
                question="",
                business_type=row.get("business_type", ""),
                difficulty=row.get("difficulty", ""),
                config=run_name,
                relevant_keys=tuple(gold),
                retrieved_keys=tuple(retrieved),
                first_relevant_rank=scored["first_relevant_rank"],
                recall_at=scored["recall_at"],
                hit_at=scored["hit_at"],
                reciprocal_rank=scored["reciprocal_rank"],
                ndcg_at_10=scored["ndcg_at_10"],
                latency_ms={},
            )
        )
    return aggregate_results(results, available_buckets=["overall"])["overall"]


def compare(published: dict, recomputed: dict, tolerance: float) -> list[str]:
    """Return one line per metric that does not reproduce, or [] when all do."""
    mismatches = []
    for key, expected in sorted(published.items()):
        if key in ("sample_count", "scored_sample_count"):
            if int(expected) != int(recomputed.get(key, -1)):
                mismatches.append(f"{key}: published {expected}, recomputed {recomputed.get(key)}")
            continue
        if not isinstance(expected, (int, float)):
            continue
        actual = recomputed.get(key)
        if actual is None:
            mismatches.append(f"{key}: published {expected}, recomputed nothing")
            continue
        if not math.isclose(float(expected), float(actual), rel_tol=0.0, abs_tol=tolerance):
            mismatches.append(f"{key}: published {expected}, recomputed {actual}")
    return mismatches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", default=None, help="verify only this run id (default: every run)")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-9,
        help="absolute tolerance when comparing floats (default 1e-9)",
    )
    parser.add_argument("--verbose", action="store_true", help="print the recomputed metrics")
    parser.add_argument(
        "--manifest",
        default=str(MANIFEST_PATH),
        help="evidence manifest to verify (default: the committed one)",
    )
    parser.add_argument(
        "--per-sample",
        default=str(PER_SAMPLE_PATH),
        help="per-sample evidence to verify (default: the committed one)",
    )
    args = parser.parse_args(argv)

    manifest_path, per_sample_path = Path(args.manifest), Path(args.per_sample)
    manifest = load_manifest(manifest_path)
    per_sample = load_per_sample(per_sample_path)

    print(f"manifest      : {_display(manifest_path)}")
    print(f"per-sample    : {_display(per_sample_path)} ({len(per_sample)} samples)")
    print(f"dataset sha256: {manifest['dataset']['sha256']}")
    print(f"git sha       : {manifest['git_sha']}")
    print(f"ES version    : {manifest['es_version']}")
    print()

    failures = 0
    for name, run in sorted(manifest["runs"].items()):
        if args.run and name != args.run:
            continue
        print(f"--- {name}  (run_id {run['run_id']})")
        print(f"    index  : {run['index']}  epoch {run['knowledge_version_epoch']}  analyzer {run['analyzer']}")
        gate = run["gate"]
        if not gate.get("results_are_benchmark"):
            print("    SKIPPED: this run is not a publishable benchmark result")
            continue
        recomputed = recompute(per_sample, name)
        mismatches = compare(run["metrics"], recomputed, args.tolerance)
        if args.verbose:
            print(f"    recomputed: {json.dumps(recomputed, sort_keys=True)}")
        if mismatches:
            failures += 1
            print(f"    MISMATCH ({len(mismatches)}):")
            for line in mismatches:
                print(f"      - {line}")
        else:
            published = {k: round(v, 6) for k, v in run["metrics"].items() if isinstance(v, float)}
            print(f"    OK: all {len(run['metrics'])} published metrics reproduced from the committed evidence")
            print(f"        {json.dumps(published, sort_keys=True)}")

    if failures:
        print(f"\nFAILED: {failures} run(s) did not reproduce.")
        return 1
    print("\nAll published metrics reproduced from committed evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
