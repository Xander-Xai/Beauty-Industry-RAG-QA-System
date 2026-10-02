"""Reproducible performance-evidence artifact contract.

The retrieval benchmark measures answer *quality*; this module measures
*service behaviour under load*. It deliberately shares the artifact discipline
already established in :mod:`benchmarks.provenance` so both harness families
behave identically when a dependency is missing.

The single rule this module exists to enforce:

    Not executed is not zero.

A run that never reached the service must never be able to emit
``p95 = 0.0`` or ``qps = 0.0`` and read as a fast, healthy system. Every
unmeasured quantity is ``None`` and the run carries an explicit status:

``EXECUTED``
    The full declared workload ran and every request completed.
``PARTIAL``
    The harness ran but some requests failed, or only part of the declared
    workload executed.
``BLOCKED``
    The workload never ran. A ``blocked_reason`` says why.

Statuses are derived from what actually happened. They are never chosen by the
caller, so a run cannot label itself ``EXECUTED`` while holding no samples.
"""

from __future__ import annotations

import csv
import io
import json
import platform
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.provenance import (
    collect_environment,
    git_provenance,
    now_utc,
    sha256_bytes,
)

PERFORMANCE_ARTIFACT_DIR = Path("artifacts/performance")

STATUS_EXECUTED = "EXECUTED"
STATUS_PARTIAL = "PARTIAL"
STATUS_BLOCKED = "BLOCKED"

VALID_STATUSES = frozenset({STATUS_EXECUTED, STATUS_PARTIAL, STATUS_BLOCKED})

#: Written by ``write_artifact`` for every run.
REQUIRED_ARTIFACT_FILES = (
    "metadata.json",
    "environment.json",
    "workload.json",
    "latency_metrics.json",
    "throughput_metrics.json",
    "errors.json",
    "report.md",
)

#: Optional passthrough files. Never required, never fabricated.
OPTIONAL_ARTIFACT_FILES = (
    "raw_locust_stats.csv",
    "raw_failures.csv",
)

#: Keys whose value is a measurement. ``None`` means "not measured"; ``0`` is a
#: real observation of zero. Collapsing the two is the failure this guards.
_MEASURED_KEYS = ("p50", "p95", "p99", "avg", "min", "max", "qps", "requests_per_second")


class PerformanceArtifactError(ValueError):
    """Raised when an artifact would misreport what actually happened."""


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile, or ``None`` when nothing was observed.

    ``None`` rather than ``0.0`` is the whole point: an empty sample set has no
    latency, which is not the same as a latency of zero milliseconds.
    """
    if not values:
        return None
    if not 0 < p <= 100:
        raise PerformanceArtifactError(f"percentile must be in (0, 100], got {p!r}")
    ordered = sorted(values)
    index = int(len(ordered) * p / 100)
    return round(ordered[min(index, len(ordered) - 1)], 2)


def summarize_latency(values: list[float]) -> dict[str, float | None]:
    """Return ``count`` plus optional ``min``/``avg``/``max``/``p50``/``p95``/``p99``.

    With no observations only ``count`` is present and is ``0``. No latency key
    appears at all, so a consumer cannot mistake absence for zero.
    """
    count = len(values)
    if count == 0:
        return {"count": 0}
    ordered = sorted(values)
    return {
        "count": count,
        "min": round(ordered[0], 2),
        "avg": round(sum(ordered) / count, 2),
        "max": round(ordered[-1], 2),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
    }


def compute_qps(total_requests: int, duration_seconds: float | None) -> float | None:
    """Throughput for a completed run, else ``None``.

    A zero-length or unmeasured window has no throughput. Returning ``0.0``
    here would make "the server was down" indistinguishable from "the server was
    idle", which is the exact confusion the artifact must not create.
    """
    if total_requests <= 0:
        return None
    if duration_seconds is None or duration_seconds <= 0:
        return None
    return round(total_requests / duration_seconds, 4)


@dataclass
class Workload:
    """Declared workload. Recorded verbatim so a run can be reproduced."""

    users: int | None = None
    spawn_rate: float | None = None
    duration_seconds: float | None = None
    target_base: str | None = None
    endpoints: tuple[str, ...] = ()
    authenticated: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "users": self.users,
            "spawn_rate": self.spawn_rate,
            "duration_seconds": self.duration_seconds,
            "target_base": self.target_base,
            "endpoints": list(self.endpoints),
            "authenticated": self.authenticated,
            **({"extra": dict(self.extra)} if self.extra else {}),
        }


@dataclass
class RequestTally:
    """Observed request outcomes."""

    total: int = 0
    success: int = 0
    failure: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"total": self.total, "success": self.success, "failure": self.failure}


def derive_status(
    *,
    blocked_reason: str | None,
    requests: RequestTally,
    declared_users: int | None,
    observed_users: int | None,
) -> str:
    """Derive the run status from what happened, never from caller intent.

    ``BLOCKED`` wins over everything: if the workload did not run, no partial
    success can be reported as a healthy run.
    """
    if blocked_reason:
        return STATUS_BLOCKED
    if requests.total == 0:
        return STATUS_BLOCKED
    if requests.failure > 0:
        return STATUS_PARTIAL
    # A run that never reached the declared concurrency did not complete the
    # declared workload, even if every request it did make succeeded.
    if declared_users and observed_users is not None and observed_users < declared_users:
        return STATUS_PARTIAL
    return STATUS_EXECUTED


def _system_version() -> str | None:
    try:
        from common.config import get_config_dict

        return get_config_dict().get("system", {}).get("version")
    except Exception:
        return None


def build_metadata(
    *,
    status: str,
    blocked_reason: str | None,
    workload: Workload,
    requests: RequestTally,
    throughput_qps: float | None,
    latency: dict[str, float | None],
    duration_seconds: float | None,
    observed_users: int | None,
    limitations: list[str] | None = None,
    repo_root: Path | str = ".",
    system_version: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Assemble ``metadata.json``.

    ``git_sha``/``git_dirty`` come from the working tree, and ``git_dirty`` is
    ``None`` only when git itself could not answer, so a dirty run can never be
    mistaken for a clean one.
    """
    if status not in VALID_STATUSES:
        raise PerformanceArtifactError(f"unknown status {status!r}")
    if status == STATUS_BLOCKED and not blocked_reason:
        raise PerformanceArtifactError("a BLOCKED run must carry a blocked_reason")
    if status != STATUS_BLOCKED and blocked_reason:
        raise PerformanceArtifactError("only a BLOCKED run may carry a blocked_reason")

    git_sha, git_dirty = git_provenance(repo_root)
    return {
        "schema_version": 1,
        "kind": "performance_evidence",
        "status": status,
        "blocked_reason": blocked_reason,
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "timestamp": timestamp or now_utc(),
        "system_version": system_version if system_version is not None else _system_version(),
        "host": {
            "hostname": platform.node() or None,
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "workload": workload.to_dict(),
        "observed_users": observed_users,
        "requests": requests.to_dict(),
        "throughput": {"qps": throughput_qps},
        "latency_ms": latency,
        "duration_seconds": duration_seconds,
        "environment": {},
        "limitations": list(limitations or []),
    }


def build_environment(repo_root: Path | str = ".") -> dict[str, Any]:
    """Environment block, reusing the retrieval harness's collector.

    ``repo_root`` is accepted for symmetry with the other builders but the
    retrieval collector derives everything from the running interpreter and the
    importable modules, so there is nothing to pass in.
    """
    del repo_root  # not consumed by the shared collector
    return collect_environment()


def _no_measurement_artifact(
    *,
    kind: str,
    reason: str,
    workload: Workload,
    requests: RequestTally,
    duration_seconds: float | None,
    observed_users: int | None,
    limitations: list[str],
    repo_root: Path | str,
    system_version: str | None,
    timestamp: str | None,
    blocked_reason: str | None,
    raw_stats_csv: str | None,
    raw_failures_csv: str | None,
) -> dict[str, Any]:
    """Build the full artifact for a run that produced no measurements."""
    status = derive_status(
        blocked_reason=blocked_reason,
        requests=requests,
        declared_users=workload.users,
        observed_users=observed_users,
    )
    latency = summarize_latency([])
    qps = compute_qps(requests.total, duration_seconds)
    metadata = build_metadata(
        status=status,
        blocked_reason=blocked_reason,
        workload=workload,
        requests=requests,
        throughput_qps=qps,
        latency=latency,
        duration_seconds=duration_seconds,
        observed_users=observed_users,
        limitations=limitations,
        repo_root=repo_root,
        system_version=system_version,
        timestamp=timestamp,
    )
    throughput = {
        "qps": qps,
        "measured": qps is not None,
        "note": None if qps is not None else "throughput not measured for this run",
    }
    errors = {
        **requests.to_dict(),
        "error_rate": None if requests.total == 0 else round(requests.failure / requests.total, 6),
        "status": status,
        "blocked_reason": blocked_reason,
        "note": None if requests.total else "no request was executed",
    }
    return {
        "metadata": metadata,
        "environment": build_environment(repo_root),
        "workload": workload.to_dict(),
        "latency": latency,
        "throughput": throughput,
        "errors": errors,
        "limitations": limitations,
        "raw_stats_csv": raw_stats_csv,
        "raw_failures_csv": raw_failures_csv,
        "reason": reason,
    }


def write_artifact(
    run_dir: Path,
    *,
    kind: str = "locust",
    blocked_reason: str | None = None,
    workload: Workload | None = None,
    latency_samples: list[float] | None = None,
    requests: RequestTally | None = None,
    duration_seconds: float | None = None,
    observed_users: int | None = None,
    limitations: list[str] | None = None,
    repo_root: Path | str = ".",
    system_version: str | None = None,
    timestamp: str | None = None,
    raw_stats_csv: str | None = None,
    raw_failures_csv: str | None = None,
) -> dict[str, Any]:
    """Write the seven-file performance artifact and return its summary.

    The returned dict is the artifact as written, so a test can assert on the
    files rather than on an intermediate structure.
    """
    workload = workload or Workload()
    requests = requests or RequestTally()
    samples = list(latency_samples or [])

    base_limitations = [
        "Single-host run; this is not a production cluster, HA or multi-node measurement.",
        "Workload is a synthetic query mix, not real user traffic.",
    ]
    all_limitations = list(dict.fromkeys([*(limitations or []), *base_limitations]))

    if blocked_reason or not samples:
        reason = blocked_reason or "no latency sample was recorded, so the workload did not execute"
        artifact = _no_measurement_artifact(
            kind=kind,
            reason=reason,
            workload=workload,
            requests=requests,
            duration_seconds=duration_seconds,
            observed_users=observed_users,
            limitations=all_limitations,
            repo_root=repo_root,
            system_version=system_version,
            timestamp=timestamp,
            blocked_reason=blocked_reason or reason,
            raw_stats_csv=raw_stats_csv,
            raw_failures_csv=raw_failures_csv,
        )
    else:
        latency = summarize_latency(samples)
        qps = compute_qps(requests.total, duration_seconds)
        status = derive_status(
            blocked_reason=None,
            requests=requests,
            declared_users=workload.users,
            observed_users=observed_users,
        )
        metadata = build_metadata(
            status=status,
            blocked_reason=None,
            workload=workload,
            requests=requests,
            throughput_qps=qps,
            latency=latency,
            duration_seconds=duration_seconds,
            observed_users=observed_users,
            limitations=all_limitations,
            repo_root=repo_root,
            system_version=system_version,
            timestamp=timestamp,
        )
        artifact = {
            "metadata": metadata,
            "environment": build_environment(repo_root),
            "workload": workload.to_dict(),
            "latency": latency,
            "throughput": {
                "qps": qps,
                "measured": qps is not None,
                "note": None if qps is not None else "duration was not measurable; throughput not derived",
            },
            "errors": {
                **requests.to_dict(),
                "error_rate": round(requests.failure / requests.total, 6) if requests.total else None,
                "status": status,
                "blocked_reason": None,
                "note": None,
            },
            "limitations": all_limitations,
            "raw_stats_csv": raw_stats_csv,
            "raw_failures_csv": raw_failures_csv,
            "reason": None,
        }

    _materialize(run_dir, artifact)
    return artifact


def _materialize(run_dir: Path, artifact: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "metadata.json", artifact["metadata"])
    _write_json(run_dir / "environment.json", artifact["environment"])
    _write_json(run_dir / "workload.json", artifact["workload"])
    _write_json(run_dir / "latency_metrics.json", artifact["latency"])
    _write_json(run_dir / "throughput_metrics.json", artifact["throughput"])
    _write_json(run_dir / "errors.json", artifact["errors"])
    (run_dir / "report.md").write_text(build_report(artifact), encoding="utf-8")

    if artifact.get("raw_stats_csv"):
        (run_dir / "raw_locust_stats.csv").write_text(artifact["raw_stats_csv"], encoding="utf-8")
    if artifact.get("raw_failures_csv"):
        (run_dir / "raw_failures.csv").write_text(artifact["raw_failures_csv"], encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fmt(value: float | int | None, unit: str = "") -> str:
    """Render a measurement, or say plainly that it was not measured."""
    if value is None:
        return "not measured"
    return f"{value}{unit}"


def build_report(artifact: dict[str, Any]) -> str:
    """Human-readable report.

    The first line states the status, and unmeasured values render as
    "not measured" rather than as a number.
    """
    meta = artifact["metadata"]
    workload = artifact["workload"]
    latency = artifact["latency"]
    throughput = artifact["throughput"]
    errors = artifact["errors"]
    status = meta["status"]

    lines: list[str] = []
    lines.append(f"# Performance evidence — status `{status}`")
    lines.append("")
    if status == STATUS_BLOCKED:
        lines.append("**No workload was executed.** This artifact records an attempt, not a result.")
        lines.append("")
        lines.append(f"- `blocked_reason`: {meta['blocked_reason'] or 'unrecorded'}")
        lines.append(f"- latency: {_fmt(latency.get('p95'), ' ms')}")
        lines.append(f"- throughput: {_fmt(throughput.get('qps'), ' req/s')}")
    else:
        lines.append(f"{errors['success']}/{errors['total']} requests succeeded, {errors['failure']} failed.")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| p50 latency | {_fmt(latency.get('p50'), ' ms')} |")
        lines.append(f"| p95 latency | {_fmt(latency.get('p95'), ' ms')} |")
        lines.append(f"| p99 latency | {_fmt(latency.get('p99'), ' ms')} |")
        lines.append(f"| throughput | {_fmt(throughput.get('qps'), ' req/s')} |")
        lines.append(f"| error rate | {_fmt(errors.get('error_rate'))} |")

    lines.append("")
    lines.append("## Workload")
    lines.append("")
    lines.append(f"- declared users: {_fmt(workload.get('users'))}")
    lines.append(f"- spawn rate: {_fmt(workload.get('spawn_rate'))}")
    lines.append(f"- duration: {_fmt(workload.get('duration_seconds'), ' s')}")
    lines.append(f"- target: {workload.get('target_base') or 'unrecorded'}")
    lines.append(f"- authenticated: {workload.get('authenticated')}")

    lines.append("")
    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- `git_sha`: {meta['git_sha'] or 'unavailable'}")
    lines.append(f"- `git_dirty`: {meta['git_dirty'] if meta['git_dirty'] is not None else 'unavailable'}")
    lines.append(f"- `system_version`: {meta['system_version'] or 'unavailable'}")
    lines.append(f"- `timestamp`: {meta['timestamp']}")
    lines.append(f"- host: {meta['host'].get('hostname') or 'unavailable'}")

    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    for limitation in artifact.get("limitations", []):
        lines.append(f"- {limitation}")
    lines.append("")
    lines.append(
        "This is repository performance evidence. It is not a production SLO result and it "
        "does not reproduce historical production traffic."
    )
    lines.append("")
    return "\n".join(lines)


_RUN_ID_LOCK = threading.Lock()
_RUN_ID_SEEN: set[str] = set()


def new_run_id(prefix: str = "perf") -> str:
    """Unique run identifier.

    Microsecond resolution plus a process-local disambiguator: two runs started
    in the same second must not resolve to the same artifact directory, or the
    second would silently overwrite the first.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    with _RUN_ID_LOCK:
        candidate = f"{prefix}-{stamp}"
        suffix = 1
        while candidate in _RUN_ID_SEEN:
            suffix += 1
            candidate = f"{prefix}-{stamp}-{suffix}"
        _RUN_ID_SEEN.add(candidate)
    return candidate


@dataclass
class LatencyStats:
    """Accumulating latency sample set with null-aware statistics.

    Lives here rather than in the Locust file so the rule is defined once and is
    importable without importing Locust (which monkey-patches the interpreter
    and cannot be imported inside a normal pytest process).
    """

    values: list[float] = field(default_factory=list)

    def add(self, value: float) -> None:
        self.values.append(value)

    @property
    def count(self) -> int:
        return len(self.values)

    def percentile(self, p: float) -> float | None:
        return percentile(self.values, p)

    @property
    def p50(self) -> float | None:
        return percentile(self.values, 50)

    @property
    def p95(self) -> float | None:
        return percentile(self.values, 95)

    @property
    def p99(self) -> float | None:
        return percentile(self.values, 99)

    @property
    def avg(self) -> float | None:
        if not self.values:
            return None
        return round(sum(self.values) / len(self.values), 2)

    @property
    def min(self) -> float | None:
        return round(min(self.values), 2) if self.values else None

    @property
    def max(self) -> float | None:
        return round(max(self.values), 2) if self.values else None

    def to_dict(self) -> dict[str, float | None]:
        return summarize_latency(self.values)


def stats_to_csv(rows: list[dict[str, Any]]) -> str:
    """Serialize passthrough Locust stats rows; never synthesized from nothing."""
    if not rows:
        return ""
    buffer = io.StringIO()
    fieldnames = sorted({key for row in rows for key in row})
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key) for key in fieldnames})
    return buffer.getvalue()


def artifact_digest(artifact: dict[str, Any]) -> str:
    """Stable digest over the measurement-bearing part of an artifact."""
    payload = {
        "status": artifact["metadata"]["status"],
        "requests": artifact["metadata"]["requests"],
        "latency": artifact["latency"],
        "throughput": artifact["throughput"],
        "errors": artifact["errors"],
        "workload": artifact["workload"],
    }
    return sha256_bytes(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    from benchmarks.performance_cli import main

    raise SystemExit(main())
