"""Command-line entry point for the retrieval benchmark.

Examples
--------
List what the current environment can actually execute::

    python -m benchmarks.retrieval_benchmark --list-configs

Attempt a real run (writes an artifact set even when everything is blocked)::

    python -m benchmarks.retrieval_benchmark --config bm25 --limit 5

The CLI has no flag that installs a fake retriever. A synthetic retriever can
only be supplied programmatically by tests, and any run that used one is flagged
in its artifacts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benchmarks import backends, report
from benchmarks.dataset import (
    DEFAULT_DATASET_PATH,
    DatasetError,
    bucket_coverage,
    load_queries,
    load_rows,
    sample_ids_hash,
    sha256_file,
)
from benchmarks.models import STATUS_EXECUTED
from benchmarks.provenance import (
    collect_environment,
    effective_retrieval_config,
    git_provenance,
    new_run_id,
    now_utc,
    render_environment,
    sha256_json,
    write_gitignore_entry,
)
from benchmarks.runner import DEFAULT_TOP_K, MIN_TOP_K, run_configuration, summarize_run, write_per_query_jsonl

DEFAULT_OUTPUT_DIR = "artifacts/benchmarks"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.retrieval_benchmark",
        description="Deterministic retrieval benchmark (no RAGAS evaluator required).",
    )
    parser.add_argument("--config", help="single configuration to run")
    parser.add_argument("--configs", help="comma-separated configurations to run")
    parser.add_argument("--dataset", default=DEFAULT_DATASET_PATH, help="golden set JSONL path")
    parser.add_argument("--limit", type=int, default=None, help="only use the first N samples")
    parser.add_argument("--sample-id", action="append", default=None, help="run only this sample id (repeatable)")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="retrieval depth (default 10)")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="artifact root directory")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow running with a dirty working tree (recorded as git_dirty=true)",
    )
    parser.add_argument(
        "--require-results",
        action="store_true",
        help="exit non-zero when no configuration executed (useful to fail CI on blocked runs)",
    )
    parser.add_argument("--list-configs", action="store_true", help="print configuration availability and exit")
    return parser


def _resolve_configs(args: argparse.Namespace) -> list[str]:
    if args.config and args.configs:
        raise SystemExit("use either --config or --configs, not both")
    if args.config:
        return [args.config]
    if args.configs:
        requested = [item.strip() for item in args.configs.split(",") if item.strip()]
        if not requested:
            # `--configs ,,` must not become an empty experiment that still
            # writes a "multi" artifact and exits successfully.
            raise SystemExit("--configs was provided but contained no configuration names")
        return requested
    return ["bm25", "dense", "hybrid_rrf"]


def _print_configs(dataset_queries: int) -> int:
    print("configurations (availability probed live):\n")
    for name, description in backends.CONFIG_DESCRIPTIONS.items():
        availability = backends.evaluate_config(name, dataset_queries)
        state = "EXECUTABLE" if availability.available else "BLOCKED"
        print(f"  {name:36s} {state:12s} {description}")
        if not availability.available:
            print(f"  {'':36s} reasons: {availability.describe()}")
            if availability.detail:
                print(f"  {'':36s} detail:  {availability.detail}")
    print("\nNote: a configuration is only reported EXECUTABLE when every required backend")
    print("and the benchmark corpus answered a real probe. No stand-in retriever is used.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    dataset_path = Path(args.dataset)
    if not dataset_path.is_file():
        print(f"dataset not found: {dataset_path}", file=sys.stderr)
        return 2

    try:
        rows = load_rows(dataset_path)
    except DatasetError as exc:
        print(f"dataset error: {exc}", file=sys.stderr)
        return 2

    if args.list_configs:
        return _print_configs(len(rows))

    if args.top_k < MIN_TOP_K:
        print(
            f"--top-k must be >= {MIN_TOP_K} so every reported metric cutoff (Recall@10, MRR@10, "
            f"NDCG@10) is observable; got {args.top_k}. Lowering it would silently understate them.",
            file=sys.stderr,
        )
        return 2

    configs = _resolve_configs(args)
    unknown = [name for name in configs if name not in backends.CONFIG_DESCRIPTIONS]
    if unknown:
        print(f"unknown configuration(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    git_sha, git_dirty = git_provenance(repo_root)
    if git_dirty and not args.allow_dirty:
        print(
            "refusing to run on a dirty working tree; commit your changes or pass --allow-dirty "
            "(the artifact will record git_dirty=true)",
            file=sys.stderr,
        )
        return 3

    try:
        queries = load_queries(dataset_path, limit=args.limit, sample_ids=args.sample_id)
    except DatasetError as exc:
        print(f"dataset error: {exc}", file=sys.stderr)
        return 2

    run_id = new_run_id()
    effective_config = effective_retrieval_config()
    config_payload = {
        "configs": configs,
        "top_k": args.top_k,
        "dataset_sha256": sha256_file(dataset_path),
        # Snapshot of the settings that actually apply, so two runs against
        # different indexes/collections/model revisions cannot share a hash.
        "effective_config": effective_config,
    }
    metadata = {
        "run_id": run_id,
        "timestamp_utc": now_utc(),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "dataset_path": str(dataset_path),
        "dataset_sha256": sha256_file(dataset_path),
        "sample_count": len(queries),
        "sample_ids_hash": sample_ids_hash(queries),
        "config_name": configs[0] if len(configs) == 1 else "multi",
        "config_sha256": sha256_json(config_payload),
        # The snapshot travels with the hash so a run can be reproduced
        # without guessing which endpoint/collection/model was in effect.
        "effective_config": effective_config,
        "requested_configs": configs,
        "top_k": args.top_k,
        "allow_dirty": bool(args.allow_dirty),
        "synthetic_retriever": False,
    }
    environment = render_environment(collect_environment())
    # Coverage describes the samples this run actually used, not the whole file.
    coverage = bucket_coverage([query.raw for query in queries])

    runs = [run_configuration(name, queries, top_k=args.top_k) for name in configs]
    summary = summarize_run(runs, available_buckets=coverage.get("available_buckets"))
    # A blocked run contains no retrieval-quality result, so the artifact must
    # not advertise itself as benchmark evidence.
    metadata["results_are_benchmark"] = bool(summary.get("any_results"))

    output_root = Path(args.output_dir)
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    rows_written = write_per_query_jsonl(run_dir / "per_query_results.jsonl", runs)

    report.write_artifacts(
        output_root=output_root,
        run_id=run_id,
        metadata=metadata,
        environment=environment,
        summary=summary,
        coverage=coverage,
        per_query_rows=rows_written,
        synthetic_retriever=False,
    )

    missing = report.verify_artifact_set(run_dir)
    if missing:
        print(f"artifact verification failed, missing: {missing}", file=sys.stderr)
        return 4

    if repo_root.exists():
        write_gitignore_entry(repo_root, f"{DEFAULT_OUTPUT_DIR}/*")

    executed = [run.outcome.config_name for run in runs if run.outcome.status == STATUS_EXECUTED]
    print(f"run_id: {run_id}")
    print(f"artifacts: {run_dir}")
    print(f"requested: {', '.join(configs)}")
    print(f"executed: {', '.join(executed) if executed else 'none'}")
    for run in runs:
        if run.outcome.status != STATUS_EXECUTED:
            print(f"  {run.outcome.config_name}: {run.outcome.status} — {run.outcome.reason}")

    if not executed:
        print(
            "\nNo configuration executed: this run contains no retrieval-quality result. "
            "The artifact set records the blocking reasons.",
            file=sys.stderr,
        )
        if args.require_results:
            return 5
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
