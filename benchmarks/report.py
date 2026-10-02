"""Artifact writing and Markdown reporting.

Artifacts are grouped per run id:

``artifacts/benchmarks/<run-id>/``
    ``metadata.json``, ``environment.json``, ``retrieval_metrics.json``,
    ``latency_metrics.json``, ``per_query_results.jsonl``, ``report.md``

A run in which no configuration executed still writes the artifact set, with the
metrics files present but empty and the reasons recorded. That keeps "blocked"
distinguishable from "never ran" without inventing numbers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.models import STATUS_EXECUTED

REQUIRED_ARTIFACT_FILES = (
    "metadata.json",
    "environment.json",
    "retrieval_metrics.json",
    "latency_metrics.json",
    "per_query_results.jsonl",
    "report.md",
)


def _dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _metric_table(block: dict[str, Any]) -> list[str]:
    header = "| metric | value |"
    divider = "|---|---|"
    rows = [header, divider]
    for key in (
        "sample_count",
        "scored_sample_count",
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "recall_at_10",
        "hit_rate_at_1",
        "hit_rate_at_3",
        "hit_rate_at_5",
        "hit_rate_at_10",
        "mrr_at_10",
        "ndcg_at_10",
    ):
        if key in block:
            rows.append(f"| {key} | {_fmt(block[key])} |")
    return rows


def _latency_table(block: dict[str, Any]) -> list[str]:
    rows = [
        "| stage | count | mean | p50 | p95 | p99 |",
        "|---|---|---|---|---|---|",
    ]
    for stage, stats in block.items():
        rows.append(
            "| {stage} | {count} | {mean} | {p50} | {p95} | {p99} |".format(
                stage=stage,
                count=stats.get("count", 0),
                mean=_fmt(stats.get("mean"), 2),
                p50=_fmt(stats.get("p50"), 2),
                p95=_fmt(stats.get("p95"), 2),
                p99=_fmt(stats.get("p99"), 2),
            )
        )
    return rows


def build_report(
    metadata: dict[str, Any],
    environment: dict[str, Any],
    summary: dict[str, Any],
    coverage: dict[str, Any],
    per_query_rows: int,
    synthetic_retriever: bool,
) -> str:
    """Render the human-readable run report."""
    lines: list[str] = ["# Benchmark Run", ""]

    lines += ["## Provenance", ""]
    for key in (
        "run_id",
        "timestamp_utc",
        "git_sha",
        "git_dirty",
        "dataset_path",
        "dataset_sha256",
        "sample_count",
        "sample_ids_hash",
        "config_sha256",
    ):
        lines.append(f"- `{key}`: {_fmt(metadata.get(key))}")
    lines.append(f"- requested configs: {', '.join(metadata.get('requested_configs') or []) or 'none'}")
    lines.append("")

    lines += ["## Dataset", ""]
    lines.append(f"- sample_count: {coverage.get('sample_count')}")
    lines.append(f"- relevance matching: {coverage.get('relevance_level')}")
    lines.append(f"- available buckets: {', '.join(coverage.get('available_buckets') or [])}")
    lines.append("- unavailable buckets: " + (", ".join(coverage.get("unavailable_buckets") or []) or "none"))
    lines.append(f"- field coverage: `{json.dumps(coverage.get('field_coverage'), ensure_ascii=False)}`")
    lines.append("")

    lines += ["## Configuration", ""]
    lines.append("| config | status | reason |")
    lines.append("|---|---|---|")
    for name, entry in (summary.get("configs") or {}).items():
        lines.append(f"| `{name}` | {entry.get('status')} | {entry.get('reason') or ''} |")
    lines.append("")

    executed = [
        name for name, entry in (summary.get("configs") or {}).items() if entry.get("status") == STATUS_EXECUTED
    ]

    if not executed:
        lines += [
            "## Overall Metrics",
            "",
            "No configuration executed in this run, so no retrieval metric was produced.",
            "",
        ]
    for name in executed:
        entry = summary["configs"][name]
        metrics = entry.get("metrics", {})
        lines += [f"## Overall Metrics — `{name}`", ""]
        lines += _metric_table(metrics.get("overall", {}))
        lines.append("")
        for attribute, label in (("business_type", "Business Type"), ("difficulty", "Difficulty")):
            blocks = metrics.get(f"by_{attribute}")
            if not blocks:
                # The dataset does not carry this field; printing an empty or
                # all-unknown table would overstate what the run measured.
                continue
            lines += [f"## Metrics by {label} — `{name}`", ""]
            for bucket, block in blocks.items():
                lines += [f"### {bucket}", ""]
                lines += _metric_table(block)
                lines.append("")
        lines += [f"## Latency — `{name}`", ""]
        lines += _latency_table(entry.get("latency", {}))
        lines.append("")

    lines += ["## Failed / Skipped Queries", ""]
    partial_failures: list[str] = []
    for name, entry in (summary.get("configs") or {}).items():
        failed_samples = entry.get("failure_count") or 0
        if entry.get("status") == STATUS_EXECUTED and failed_samples:
            # Metrics were produced from a reduced sample; that must be visible
            # next to the numbers.
            partial_failures.append(f"`{name}`: {failed_samples} sample(s) failed during retrieval")
    if per_query_rows == 0:
        lines.append("- none: no configuration produced per-query results in this run")
    else:
        lines.append(f"- per-query result rows written: {per_query_rows}")
    lines += [f"- {item}" for item in partial_failures]
    for name, entry in (summary.get("configs") or {}).items():
        if entry.get("status") != STATUS_EXECUTED:
            lines.append(f"- `{name}`: {entry.get('status')} — {entry.get('reason')}")
    if partial_failures:
        lines.append("")
        lines.append(
            "> The metrics above were computed from fewer samples than requested; "
            "the failed sample ids are listed in `retrieval_metrics.json`."
        )
    lines.append("")

    lines += ["## Environment", ""]
    for key in (
        "python_version",
        "platform",
        "cpu",
        "ram_bytes",
        "torch_version",
        "cuda_available",
        "qdrant_version",
        "elasticsearch_version",
    ):
        lines.append(f"- `{key}`: {_fmt(environment.get(key))}")
    lines.append("")

    lines += ["## Limitations", ""]
    lines.append("- These results are repository benchmark results, not historical production metrics.")
    if synthetic_retriever:
        lines.append(
            "- **This run used a synthetic fixture retriever.** The numbers below verify metric and "
            "aggregation correctness only; they are NOT a retrieval-quality benchmark result."
        )
    if not executed:
        lines.append("- No configuration executed, so this run contains no retrieval-quality evidence.")
    lines.append("- Graded relevance is not available in the dataset; NDCG uses binary relevance.")
    lines.append("- `visual_required` and stored complexity labels are absent from the dataset and are not produced.")
    lines.append("")
    return "\n".join(lines)


def build_comparison(summary: dict[str, Any]) -> str:
    """Comparison table across executed configurations (values read from artifacts)."""
    executed = [
        name for name, entry in (summary.get("configs") or {}).items() if entry.get("status") == STATUS_EXECUTED
    ]
    lines = ["# Configuration Comparison", ""]
    if not executed:
        lines.append("No configuration executed; no comparison is available.")
        return "\n".join(lines) + "\n"
    lines += [
        "| config | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR@10 | NDCG@10 | P50 | P95 | P99 | sample_count |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in executed:
        entry = summary["configs"][name]
        overall = entry.get("metrics", {}).get("overall", {})
        latency = entry.get("latency", {}).get("total_retrieval_ms", {})
        lines.append(
            "| `{c}` | {r1} | {r3} | {r5} | {r10} | {mrr} | {ndcg} | {p50} | {p95} | {p99} | {n} |".format(
                c=name,
                r1=_fmt(overall.get("recall_at_1")),
                r3=_fmt(overall.get("recall_at_3")),
                r5=_fmt(overall.get("recall_at_5")),
                r10=_fmt(overall.get("recall_at_10")),
                mrr=_fmt(overall.get("mrr_at_10")),
                ndcg=_fmt(overall.get("ndcg_at_10")),
                p50=_fmt(latency.get("p50"), 2),
                p95=_fmt(latency.get("p95"), 2),
                p99=_fmt(latency.get("p99"), 2),
                n=overall.get("sample_count"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def write_artifacts(
    output_root: Path,
    run_id: str,
    metadata: dict[str, Any],
    environment: dict[str, Any],
    summary: dict[str, Any],
    coverage: dict[str, Any],
    per_query_rows: int,
    synthetic_retriever: bool,
) -> Path:
    """Write the artifact set for one run and return the run directory."""
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _dump(run_dir / "metadata.json", metadata)
    _dump(run_dir / "environment.json", environment)
    _dump(run_dir / "retrieval_metrics.json", summary)
    _dump(run_dir / "latency_metrics.json", summary.get("latency") or {})
    per_query_path = run_dir / "per_query_results.jsonl"
    if not per_query_path.exists():
        # The contract requires the file even when no configuration executed, so
        # "blocked" stays distinguishable from "never produced an artifact".
        per_query_path.write_text("", encoding="utf-8")
    report = build_report(metadata, environment, summary, coverage, per_query_rows, synthetic_retriever)
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    if len(executed_configs(summary)) > 1:
        (run_dir / "comparison.md").write_text(build_comparison(summary), encoding="utf-8")
    return run_dir


def executed_configs(summary: dict[str, Any]) -> list[str]:
    return [name for name, entry in (summary.get("configs") or {}).items() if entry.get("status") == STATUS_EXECUTED]


def verify_artifact_set(run_dir: Path) -> list[str]:
    """Return the names of required artifact files that are missing."""
    return [name for name in REQUIRED_ARTIFACT_FILES if not (run_dir / name).is_file()]
