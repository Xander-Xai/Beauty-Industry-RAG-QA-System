"""Real CrossEncoder rerank validation: smoke test and real-vs-fallback A/B.

Why this exists
---------------
`retrieval/rerank_status.py` answers *"are the weights installed?"*. It cannot
answer *"do the weights actually improve the ranking?"* — and this repository has
never been able to answer that, because the second-stage reranker has only ever
run its deterministic fallback.

That creates a specific risk this module exists to prevent. The fallback returns
the BiEncoder ordering untouched with ``ce_score_ensemble = 0``. It is
**deterministic and correct as a degradation**, but its output is not
CrossEncoder inference. If an A/B or a latency report is produced without
labelling which path ran, "CrossEncoder enabled" and "CrossEncoder fell back"
become indistinguishable — and the numbers get read as reranking evidence.

So every result here carries a provenance label:

``cross_encoder``
    The configured weights were loaded and produced the scores.
``deterministic_fallback``
    No weights. The ranking is BiEncoder order. **Not** a reranking result, and
    the artifact says so in the same field a reader looks at first.

Nothing here fabricates. With no weights the entry points exit ``PENDING``
(3) and emit a report whose ``status`` is ``PENDING`` with the exact missing
asset named — never a zero, never a placeholder, never a "no improvement"
verdict presented as a measurement.

This module **never downloads weights**. Loading is attempted only from the
configured local paths. That is a deliberate constraint: an evaluation harness
that pulls multi-gigabyte checkpoints as a side effect of a "smoke test" is not
something to run unattended.

Usage::

    # status only, no model load
    python3 -m retrieval.rerank_validation --smoke

    # real-vs-fallback comparison (requires configured weights)
    python3 -m retrieval.rerank_validation --compare --report-dir artifacts/rerank

    # fail CI when the reranker is unavailable
    python3 -m retrieval.rerank_validation --smoke --require-real
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Where a real run writes its artifact.
DEFAULT_REPORT_DIR = "artifacts/rerank"

#: Exit codes. Distinct from the benchmark's, because the states are distinct:
#: a pending asset is not a broken configuration and not a failed measurement.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG_ERROR = 2
EXIT_PENDING = 3

#: Provenance labels. Every measured result carries exactly one.
PROVENANCE_CROSS_ENCODER = "cross_encoder"
PROVENANCE_FALLBACK = "deterministic_fallback"

PROVENANCE_VALUES = (PROVENANCE_CROSS_ENCODER, PROVENANCE_FALLBACK)


def candidate_order(pair: Pair) -> list[str]:
    """Deterministic presentation order for one pair's candidates.

    The positive document is deliberately **not** first. Presenting it first
    makes both sides of the comparison score ``1.0`` by construction: the
    fallback preserves input order, and a correct reranker also puts it first.
    ``top1_rate_delta`` would then be pinned at ``0.0`` no matter what the model
    does, so a reranker that reorders everything wrongly could not be
    distinguished from a perfect one. Rotating by a hash of ``pair_id`` keeps the
    run reproducible while forcing both sides to actually do the ranking.

    The rotation is a function of ``pair_id`` alone, so the real and fallback
    sides are scored on the identical presentation order.
    """
    candidates = [pair.positive, *pair.distractors]
    if len(candidates) < 2:
        return candidates
    # Offset is drawn from 1..n-1 rather than 0..n-1, so the positive is
    # guaranteed never to be presented first — the trivial pass is unavailable.
    offset = 1 + int(hashlib.sha256(pair.pair_id.encode("utf-8")).hexdigest(), 16) % (len(candidates) - 1)
    return candidates[offset:] + candidates[:offset]


@dataclass(frozen=True)
class Pair:
    """One (query, positive-document, distractor) judgement.

    ``positive`` is the document a correct cross-encoder should rank first.
    These are hand-written fixtures, not retrieved results: a ranking test needs
    a known-correct answer, and reusing the golden set here would need the
    identity mapping that does not exist yet.
    """

    pair_id: str
    query: str
    positive: str
    distractors: tuple[str, ...]


@dataclass
class RankResult:
    """Ranking outcome plus the provenance of the ranking that produced it."""

    provenance: str
    order: list[str]
    scores: dict[str, float]
    latency_ms: float | None
    model_revision: str | None = None

    @property
    def is_real_cross_encoder(self) -> bool:
        return self.provenance == PROVENANCE_CROSS_ENCODER


@dataclass
class ValidationReport:
    """Everything one invocation observed, in one serialisable object."""

    status: str  # "OK" | "PENDING" | "FAILED"
    reason: str
    provenance: str | None = None
    model_revision: str | None = None
    git_sha: str | None = None
    git_dirty: bool | None = None
    hardware: dict[str, Any] = field(default_factory=dict)
    pair_count: int = 0
    smoke: dict[str, Any] = field(default_factory=dict)
    comparison: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    degradations: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_reranking_evidence(self) -> bool:
        """True only when real CrossEncoder inference produced these numbers.

        This is the single field a reader should check before quoting any
        ranking improvement.
        """
        return self.status == "OK" and self.provenance == PROVENANCE_CROSS_ENCODER


#: Hand-written ranking fixtures. Deliberately short: this proves the model
#: loads, scores and moves a known-correct document up — it is a smoke test, and
#: it is not presented as a benchmark.
SMOKE_PAIRS: tuple[Pair, ...] = (
    Pair(
        pair_id="smoke-ing-01",
        query="烟酰胺在化妆品中的最大允许浓度是多少？",
        positive="烟酰胺在驻留类产品中的最大允许浓度为2.0%，在 rinse-off 类产品中为5.0%。",
        distractors=(
            "烟酰胺具有美白和抗炎作用，是常见的化妆品功效成分。",
            "化妆品备案需要提交产品配方表和产品检验报告。",
            "今天天气晴朗，适合外出。",
        ),
    ),
    Pair(
        pair_id="smoke-ing-02",
        query="水杨酸在驻留类产品中的限量标准是什么？",
        positive="水杨酸在驻留类化妆品中的最大允许浓度为2.0%。",
        distractors=(
            "水杨酸属于β-羟基酸类，可用于去角质和控油。",
            "A醇可用于抗衰老护肤配方。",
            "欧盟对化妆品原料有REACH法规要求。",
        ),
    ),
    Pair(
        pair_id="smoke-reg-01",
        query="化妆品标签标识的法规要求有哪些？",
        positive="化妆品标签必须标注产品名称、净含量、使用期限、生产许可证编号和执行标准。",
        distractors=(
            "化妆品功效宣称需要提供功效评价报告摘要。",
            "防腐剂的选择需符合微生物限量要求。",
            "香精用量在配方中通常不超过限值。",
        ),
    ),
)


# ── environment capture ─────────────────────────────────────────────────────


def git_provenance(repo_root: Path = PROJECT_ROOT) -> tuple[str | None, bool | None]:
    """Return ``(sha, dirty)``. Never raises — provenance is not worth a failure."""
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, timeout=10
        )
        return (
            sha.stdout.strip() or None,
            bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
        )
    except Exception:  # noqa: BLE001 - provenance failure must not fail the run
        return None, None


def hardware_info() -> dict[str, Any]:
    """Describe the machine, including whether a GPU is actually visible.

    ``gpu_available`` is recorded as an observed fact. When it is false the run
    is a CPU run and no GPU number may be quoted from it.
    """
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cuda_available": False,
        "gpus": [],
    }
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda_available"] = bool(torch.cuda.is_available())
        if info["cuda_available"]:
            info["gpus"] = [
                {"index": index, "name": torch.cuda.get_device_name(index)}
                for index in range(torch.cuda.device_count())
            ]
            info["cuda_runtime"] = torch.version.cuda
    except Exception as exc:  # noqa: BLE001
        info["torch_error"] = f"{type(exc).__name__}: {exc}"
    return info


def model_revision(path: Path) -> str | None:
    """A content fingerprint for a loaded model directory, or ``None``.

    Two runs must be distinguishable when the weights differ, so a directory is
    hashed by its file names and sizes rather than trusting a path string.
    """
    if not path.exists() or not path.is_dir():
        return None
    digest = hashlib.sha256()
    try:
        for child in sorted(p for p in path.rglob("*") if p.is_file()):
            digest.update(str(child.relative_to(path)).encode("utf-8"))
            digest.update(b"\x1f")
            digest.update(str(child.stat().st_size).encode("utf-8"))
            digest.update(b"\x1e")
    except OSError:
        return None
    return f"sha256:{digest.hexdigest()}"


def _configured_models(config: dict | None = None) -> dict[str, str]:
    if config is None:
        from common.config import get_config_dict

        config = get_config_dict() or {}
    models = (config.get("gpu1", {}) or {}).get("models", {}) or {}
    return {
        "cross_encoder_a": str(models.get("cross_encoder_a", {}).get("model_path", "")),
        "cross_encoder_b": str(models.get("cross_encoder_b", {}).get("model_path", "")),
    }


# ── smoke test ──────────────────────────────────────────────────────────────


def run_smoke(pairs: tuple[Pair, ...] = SMOKE_PAIRS) -> ValidationReport:
    """Load the configured CrossEncoders and score the smoke pairs.

    Returns ``status="PENDING"`` when the weights or ``sentence_transformers``
    are absent, naming the exact missing asset. That is the expected state of
    this repository and is not an error.
    """
    from retrieval.rerank_status import rerank_weights_status

    git_sha, git_dirty = git_provenance()
    hardware = hardware_info()
    status = rerank_weights_status()
    base = {
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "hardware": hardware,
        "pair_count": len(pairs),
        "models": status["models"],
        "weights_status": status["status"],
        "evidence_gate_mode": status["evidence_gate"]["mode"],
    }

    if not status["available"]:
        return ValidationReport(
            status="PENDING",
            reason=status["reason"] or "CrossEncoder weights unavailable",
            provenance=PROVENANCE_FALLBACK,
            git_sha=git_sha,
            git_dirty=git_dirty,
            hardware=hardware,
            pair_count=len(pairs),
            smoke={**base, "executed": False},
            degradations=[{"stage": "cross_encoder_load", "detail": status["reason"]}],
        )

    models = _configured_models()
    revision = model_revision(PROJECT_ROOT / models["cross_encoder_a"])
    started = time.perf_counter()
    try:
        ranker = _load_ranker(models)
    except Exception as exc:  # noqa: BLE001 - a load failure is a real finding
        return ValidationReport(
            status="FAILED",
            reason=f"CrossEncoder load failed: {type(exc).__name__}: {exc}",
            provenance=PROVENANCE_FALLBACK,
            git_sha=git_sha,
            git_dirty=git_dirty,
            hardware=hardware,
            smoke={**base, "executed": False},
            degradations=[{"stage": "cross_encoder_load", "detail": f"{type(exc).__name__}: {exc}"}],
        )
    load_seconds = time.perf_counter() - started

    latencies: list[float] = []
    promotions = 0
    for pair in pairs:
        rank_started = time.perf_counter()
        # `candidate_order` puts the positive somewhere other than first, so
        # ranking it first is a result the model has to earn.
        result = ranker.rank(candidate_order(pair), pair.query)
        latencies.append((time.perf_counter() - rank_started) * 1000.0)
        if result.order and result.order[0] == pair.positive:
            promotions += 1

    latencies.sort()
    return ValidationReport(
        status="OK",
        reason="",
        provenance=PROVENANCE_CROSS_ENCODER,
        model_revision=revision,
        git_sha=git_sha,
        git_dirty=git_dirty,
        hardware=hardware,
        smoke={
            **base,
            "executed": True,
            "load_seconds": round(load_seconds, 3),
            "positive_at_rank1": promotions,
            "positive_at_rank1_rate": promotions / len(pairs) if pairs else None,
            "latency_ms": {
                "count": len(latencies),
                "p50": _percentile(latencies, 50),
                "p95": _percentile(latencies, 95),
            },
        },
    )


class _CrossEncoderRanker:
    """Thin wrapper over the production ensemble's scoring path.

    Deliberately uses ``sentence_transformers.CrossEncoder`` the same way
    ``retrieval/cross_encoder_ensemble.py`` does, so a smoke pass reflects the
    code that actually runs online. The Platt calibration and the ensemble
    averaging live in the ensemble; this exercises the load + predict path.
    """

    def __init__(self, path_a: str, path_b: str) -> None:
        from sentence_transformers import CrossEncoder

        self.models = [CrossEncoder(path_a), CrossEncoder(path_b)]

    def rank(self, documents: list[str], query: str) -> RankResult:
        """Score every ``(query, document)`` pair **individually** and rank by it.

        A single batched ``predict`` returns one score per pair, so
        ``scores[i]`` is document *i*'s own score. Reducing the whole array with
        ``.mean()`` instead would collapse every document onto one identical
        number, which makes the sort a guaranteed no-op: the returned order
        would always equal the input order, and since :data:`SMOKE_PAIRS` always
        passes ``positive`` first, ``positive_at_rank1_rate`` would be
        structurally ``1.0`` and the fallback-vs-real ``top1_rate_delta``
        structurally ``0.0`` — an unfalsifiable "the reranker works" claim that
        no model could ever contradict. This mirrors
        ``retrieval/cross_encoder_ensemble.py:191-195``, which indexes
        ``scores_a[i]`` per candidate for the same reason.
        """
        started = time.perf_counter()
        pairs = [(query, document) for document in documents]
        per_model: list[list[float]] = []
        for model in self.models:
            raw = model.predict(pairs)
            values = [float(value) for value in list(raw)]
            if len(values) != len(documents):
                # A score vector that does not line up with the documents cannot
                # be attributed to them. Failing loudly is the only honest option:
                # padding here would silently rank documents by an invented 0.0.
                raise RuntimeError(
                    f"CrossEncoder returned {len(values)} scores for {len(documents)} documents; "
                    "refusing to rank on a misaligned score vector"
                )
            per_model.append(values)
        latency = (time.perf_counter() - started) * 1000.0
        # The ensemble averages the two models per document; this mirrors
        # retrieval/cross_encoder_ensemble.py:194 without its Platt calibration,
        # which needs calibrated inputs this smoke set does not have.
        combined = [
            (document, sum(model_scores[i] for model_scores in per_model) / len(per_model))
            for i, document in enumerate(documents)
        ]
        combined.sort(key=lambda item: item[1], reverse=True)
        return RankResult(
            provenance=PROVENANCE_CROSS_ENCODER,
            order=[document for document, _ in combined],
            scores={document: score for document, score in combined},
            latency_ms=latency,
        )


def _load_ranker(models: dict[str, str]) -> _CrossEncoderRanker:
    return _CrossEncoderRanker(models["cross_encoder_a"], models["cross_encoder_b"])


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 4)
    position = (len(values) - 1) * pct / 100.0
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight, 4)


# ── real vs fallback comparison ─────────────────────────────────────────────


def run_comparison(pairs: tuple[Pair, ...] = SMOKE_PAIRS) -> ValidationReport:
    """Compare the real CrossEncoder ordering against the deterministic fallback.

    The fallback is BiEncoder order with ``ce_score_ensemble = 0`` — the exact
    degradation ``retrieval/cross_encoder_ensemble.py:221-224`` performs. The
    comparison is what shows whether the second stage is doing anything, and the
    report states which side ran.

    With no weights the comparison is still produced for the fallback side, but
    ``status`` is ``PENDING`` and no improvement number is computed, because a
    "fallback vs fallback" comparison is not a measurement of reranking.
    """
    from retrieval.rerank_status import rerank_weights_status

    git_sha, git_dirty = git_provenance()
    hardware = hardware_info()
    status = rerank_weights_status()

    # Fallback side: it preserves whatever order it was handed, which is exactly
    # what the ensemble's except-branch returns. Same presentation order as the
    # real side, so the two rates are comparable.
    fallback_latencies: list[float] = []
    fallback_top1 = 0
    for pair in pairs:
        started = time.perf_counter()
        order = candidate_order(pair)
        fallback_latencies.append((time.perf_counter() - started) * 1000.0)
        if order[0] == pair.positive:
            fallback_top1 += 1
    fallback_latencies.sort()

    fallback_side = {
        "provenance": PROVENANCE_FALLBACK,
        "positive_at_rank1": fallback_top1,
        "positive_at_rank1_rate": fallback_top1 / len(pairs) if pairs else None,
        "note": (
            "Input order preserved with ce_score_ensemble=0. This is the degraded path, not CrossEncoder inference."
        ),
        "latency_ms": {
            "count": len(fallback_latencies),
            "p50": _percentile(fallback_latencies, 50),
            "p95": _percentile(fallback_latencies, 95),
        },
    }

    if not status["available"]:
        return ValidationReport(
            status="PENDING",
            reason=status["reason"] or "CrossEncoder weights unavailable",
            provenance=PROVENANCE_FALLBACK,
            git_sha=git_sha,
            git_dirty=git_dirty,
            hardware=hardware,
            pair_count=len(pairs),
            comparison={"fallback": fallback_side, "cross_encoder": None},
            degradations=[{"stage": "cross_encoder_load", "detail": status["reason"]}],
        )

    smoke = run_smoke(pairs)
    if smoke.status != "OK":
        # The smoke stage is the only thing that actually loads the weights and
        # runs inference, so its verdict *is* this report's verdict. Falling
        # through would publish a load failure as `status=OK` +
        # `provenance=cross_encoder`, which flips
        # :attr:`ValidationReport.is_reranking_evidence` to True and lets
        # `--require-real` exit 0 on a run that executed nothing. The
        # comparison is still written so the artifact shows which side ran.
        comparison = {
            "fallback": fallback_side,
            "cross_encoder": None,
            "top1_rate_delta": None,
        }
        return ValidationReport(
            status=smoke.status,
            reason=smoke.reason or "CrossEncoder smoke run did not complete",
            provenance=PROVENANCE_FALLBACK,
            model_revision=smoke.model_revision,
            git_sha=git_sha,
            git_dirty=git_dirty,
            hardware=hardware,
            pair_count=len(pairs),
            smoke=smoke.smoke,
            comparison=comparison,
            # No improvement number: there is no real side to compare against.
            metrics={},
            degradations=smoke.degradations
            or [{"stage": "cross_encoder_smoke", "detail": smoke.reason or "smoke run did not complete"}],
        )

    real_side = {
        "provenance": PROVENANCE_CROSS_ENCODER,
        "positive_at_rank1": smoke.smoke.get("positive_at_rank1"),
        "positive_at_rank1_rate": smoke.smoke.get("positive_at_rank1_rate"),
        "model_revision": smoke.model_revision,
        "latency_ms": smoke.smoke.get("latency_ms"),
    }
    delta = None
    if real_side["positive_at_rank1_rate"] is not None and fallback_side["positive_at_rank1_rate"] is not None:
        delta = round(real_side["positive_at_rank1_rate"] - fallback_side["positive_at_rank1_rate"], 4)

    return ValidationReport(
        status="OK",
        reason="",
        provenance=PROVENANCE_CROSS_ENCODER,
        model_revision=smoke.model_revision,
        git_sha=git_sha,
        git_dirty=git_dirty,
        hardware=hardware,
        pair_count=len(pairs),
        comparison={
            "fallback": fallback_side,
            "cross_encoder": real_side,
            "top1_rate_delta": delta,
        },
        metrics={
            "positive_at_rank1_rate_cross_encoder": real_side["positive_at_rank1_rate"],
            "positive_at_rank1_rate_fallback": fallback_side["positive_at_rank1_rate"],
        },
    )


# ── entry point ─────────────────────────────────────────────────────────────


def write_report(report: ValidationReport, report_dir: str = DEFAULT_REPORT_DIR) -> Path:
    """Persist the report next to its provenance and return the path."""
    directory = PROJECT_ROOT / report_dir
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    sha = (report.git_sha or "nogit")[:12]
    path = directory / f"rerank-validation-{stamp}-{sha}.json"
    payload = report.as_dict()
    payload["is_reranking_evidence"] = report.is_reranking_evidence
    payload["artifact_contract"] = (
        "reranking_evidence=true requires provenance=cross_encoder AND status=OK. "
        "A deterministic_fallback report is a degradation record, not a reranking result."
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Real CrossEncoder rerank smoke test and fallback comparison.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true", help="load the weights and score the smoke pairs")
    mode.add_argument(
        "--compare", action="store_true", help="compare real CrossEncoder ordering against the deterministic fallback"
    )
    parser.add_argument(
        "--require-real",
        action="store_true",
        help="exit non-zero unless real CrossEncoder inference produced the result",
    )
    parser.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)

    if args.smoke:
        report = run_smoke()
    else:
        report = run_comparison()

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"status      : {report.status}")
        print(f"provenance  : {report.provenance}")
        print(f"reranking evidence : {report.is_reranking_evidence}")
        if report.reason:
            print(f"reason      : {report.reason}")
        if report.model_revision:
            print(f"model rev   : {report.model_revision}")
        print(f"git sha     : {report.git_sha} (dirty={report.git_dirty})")
        print(f"cuda        : {report.hardware.get('cuda_available')} gpus={report.hardware.get('gpus')}")
        for degradation in report.degradations:
            print(f"degraded    : {degradation['stage']} — {degradation['detail']}")
        if not report.is_reranking_evidence:
            print()
            print(
                "This run is NOT reranking evidence: it exercised the deterministic fallback "
                "or did not run. No CrossEncoder improvement may be quoted from it."
            )

    path = write_report(report, args.report_dir)
    print(f"\nreport: {path}")

    if args.require_real and not report.is_reranking_evidence:
        return EXIT_PENDING
    if report.status == "FAILED":
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
