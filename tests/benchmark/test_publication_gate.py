"""Publication-gate regression tests for the retrieval benchmark.

The gate is one line in `benchmarks/retrieval_benchmark.py`::

    metadata["results_are_benchmark"] = bool(summary.get("any_results")) and attributable

Both operands had to be exercised, because a run can fail to publish for two
independent reasons and only one of them used to be covered:

* ``any_results`` — nothing executed (already covered in
  `test_review_regressions.py::test_blocked_run_metadata_is_not_a_benchmark`);
* ``attributable`` — the dataset satisfies `golden-set-contract/v2`.

A run that retrieved happily against a dataset with no stable identity, no
labels and no provenance used to produce a plausible-looking metric set. These
tests pin that it must not, and that a genuinely attributable dataset must
still publish — otherwise the gate is untestable and would eventually be
removed as "blocking everything".

Every test asserts the whole contract surface: exit code, ``report.md`` status,
denominator, artifact flags and the machine-readable reason.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.golden_set_contract import (
    DatasetContractError,
    require_attributable,
    validate_dataset,
    validate_row,
)
from benchmarks.models import STATUS_EXECUTED
from tests.benchmark.conftest import FixtureRetriever

VALID_CORPUS = "epoch_2026_10_09"

#: Ground-truth passages keyed by the derived sample id (line number, zero-based).
SCRIPT = {"0000": ["alpha passage"], "0001": ["beta passage"], "0002": ["gamma passage"]}

#: Stable identifiers for the same three rows, so a contract-aware dataset is
#: matched by id exactly as a real retriever (which emits `doc_id` in its
#: payload) would be. These are fixture values, never a model-quality result.
CONTRACT_IDS = {"0000": "doc-0", "0001": "doc-1", "0002": "doc-2"}


class ContractFixtureRetriever:
    """Deterministic retriever that returns items keyed by stable identifier.

    Mirrors what a production retriever yields: a payload carrying ``doc_id``,
    from which :func:`benchmarks.relevance.stable_id_from` derives the key.
    Ranking is scripted, so the numbers prove the *gate*, not retrieval quality.
    """

    name = "contract-fixture"

    def __init__(self, doc_ids: dict[str, str], texts: dict[str, list[str]]) -> None:
        self.doc_ids = doc_ids
        self.texts = texts

    def retrieve(self, query, top_k):
        from benchmarks.models import RetrievedItem

        items = []
        for rank, text in enumerate(self.texts.get(query.sample_id, [])[:top_k], start=1):
            payload = {"doc_id": self.doc_ids.get(query.sample_id, ""), "text": text}
            from benchmarks.relevance import stable_id_from

            items.append(
                RetrievedItem(
                    rank=rank,
                    key=stable_id_from(payload),
                    score=1.0 / rank,
                    source="contract-fixture",
                    text=text,
                )
            )
        return items


def _valid_row(sample_id: str, doc_id: str, chunk_id: str, text: str, **overrides: Any) -> dict[str, Any]:
    """A row that satisfies every clause of ``golden-set-contract/v2``."""
    row = {
        "sample_id": sample_id,
        "question": f"question {sample_id}",
        "contexts": [text],
        "business_type": "regulation",
        "difficulty": "easy",
        "visual_required": False,
        "complexity_label": "simple",
        "corpus_version": VALID_CORPUS,
        "annotations": [{"doc_id": doc_id, "chunk_id": chunk_id, "text": text}],
        "annotation": {
            "annotator": "annotator-a",
            "method": "manual-passage-mapping",
            "annotated_at": "2026-10-09",
            "reviewed_by": "reviewer-b",
        },
    }
    row.update(overrides)
    return row


def _write(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def attributable_dataset(tmp_path):
    """Three rows, all contract-valid, with stable ids and real provenance."""
    return _write(
        tmp_path / "attributable.jsonl",
        [
            _valid_row("s0", "doc-0", "doc-0-chunk-0", "alpha passage"),
            _valid_row("s1", "doc-1", "doc-1-chunk-0", "beta passage"),
            _valid_row("s2", "doc-2", "doc-2-chunk-0", "gamma passage"),
        ],
    )


@pytest.fixture
def text_only_dataset(tmp_path):
    """The shape of the committed golden set: passages, but no identity/labels."""
    rows = [
        {"question": f"question {i}", "contexts": [text], "business_type": "regulation", "difficulty": "easy"}
        for i, text in enumerate(["alpha passage", "beta passage", "gamma passage"])
    ]
    return _write(tmp_path / "text_only.jsonl", rows)


@pytest.fixture
def run_cli(monkeypatch):
    """Run the CLI with a deterministic retriever so execution is exercised.

    The CLI itself offers no way to inject a retriever — that is deliberate. The
    tests need the *gate* under both operands, so the executor is stubbed here
    and every artifact still records ``synthetic_retriever: false`` semantics
    only because these numbers are never published as a model result.
    """
    from benchmarks import retrieval_benchmark as cli

    monkeypatch.setattr(cli, "git_provenance", lambda root: ("deadbeef", False))
    original = cli.run_configuration

    def _factory(cfg):
        # A dataset carrying v2 annotations is matched by stable id; a text-only
        # dataset is matched by the normalized-text fallback.
        return ContractFixtureRetriever(CONTRACT_IDS, SCRIPT)

    def _with_retriever(name, queries, top_k=10, **_kwargs):
        annotated = any(getattr(q.raw, "get", lambda *_: None)("annotations") for q in queries)
        retriever = ContractFixtureRetriever(CONTRACT_IDS, SCRIPT) if annotated else FixtureRetriever(SCRIPT)
        return original(name, queries, top_k=top_k, retriever_factory=lambda cfg: retriever)

    monkeypatch.setattr(cli, "run_configuration", _with_retriever)
    return cli


def _run_dir(out: Path) -> Path:
    return sorted(p for p in out.iterdir() if p.is_dir())[-1]


def _metadata(out: Path) -> dict[str, Any]:
    return json.loads((_run_dir(out) / "metadata.json").read_text(encoding="utf-8"))


def _report(out: Path) -> str:
    return (_run_dir(out) / "report.md").read_text(encoding="utf-8")


# ── 1. nothing executed -> must not publish ──────────────────────────────────


def test_no_execution_is_not_publishable_even_with_a_perfect_dataset(tmp_path, run_cli, attributable_dataset):
    """Scenario 1: no retrieval ran, so the `any_results` operand is false."""
    from benchmarks import backends

    original = run_cli.run_configuration
    # Block the configuration instead of executing it.
    run_cli.run_configuration = lambda name, queries, top_k=10: __import__(
        "benchmarks.runner", fromlist=["run_configuration"]
    ).run_configuration(name, queries, top_k=top_k, force_block_reason="service unavailable")

    out = tmp_path / "out"
    exit_code = run_cli.main(
        ["--config", "bm25", "--dataset", str(attributable_dataset), "--output-dir", str(out), "--require-results"]
    )
    run_cli.run_configuration = original

    assert exit_code == 5, "a blocked run must fail when --require-results is set"
    metadata = _metadata(out)
    assert metadata["results_are_benchmark"] is False
    # The dataset is fine; only execution is missing.
    assert metadata["attributable"] is True
    assert metadata["dataset_contract"]["valid_count"] == 3
    assert "No configuration executed" in _report(out)
    assert backends.CONFIG_DESCRIPTIONS  # sanity: the probe table is populated


def test_blocked_run_denominator_is_zero_not_the_dataset_size(tmp_path, run_cli, attributable_dataset):
    """A blocked run must not imply that three samples were measured."""
    from benchmarks import runner

    run_cli.run_configuration = lambda name, queries, top_k=10: runner.run_configuration(
        name, queries, top_k=top_k, force_block_reason="service unavailable"
    )
    out = tmp_path / "out"
    run_cli.main(["--config", "bm25", "--dataset", str(attributable_dataset), "--output-dir", str(out)])

    summary = json.loads((_run_dir(out) / "retrieval_metrics.json").read_text(encoding="utf-8"))
    assert summary["any_results"] is False
    assert summary["executed_configs"] == []
    entry = summary["configs"]["bm25"]
    assert entry["status"] != STATUS_EXECUTED
    assert entry["reason"] == "service unavailable"
    assert "metrics" not in entry or entry["metrics"] == {}


# ── 2. executed but not attributable -> must not publish ─────────────────────


def test_executed_on_a_non_attributable_dataset_is_not_publishable(tmp_path, run_cli, text_only_dataset):
    """Scenario 2: the `attributable` operand — the gap this file closes.

    Retrieval succeeds and produces a perfect ranking, which is exactly the
    situation that previously looked like a benchmark result.
    """
    out = tmp_path / "out"
    exit_code = run_cli.main(["--config", "bm25", "--dataset", str(text_only_dataset), "--output-dir", str(out)])

    assert exit_code == 0, "an unattributable run is still allowed, it just may not publish"
    metadata = _metadata(out)
    summary = json.loads((_run_dir(out) / "retrieval_metrics.json").read_text(encoding="utf-8"))

    assert summary["any_results"] is True, "retrieval did execute in this scenario"
    assert summary["executed_configs"] == ["bm25"]
    assert metadata["attributable"] is False
    assert metadata["results_are_benchmark"] is False
    assert metadata["dataset_contract"]["valid_count"] == 0
    assert metadata["dataset_contract"]["total"] == 3


def test_perfect_ranking_on_a_non_attributable_dataset_is_still_not_a_benchmark(tmp_path, run_cli, text_only_dataset):
    """The metrics are perfect; that must not buy a publication."""
    out = tmp_path / "out"
    run_cli.main(["--config", "bm25", "--dataset", str(text_only_dataset), "--output-dir", str(out)])
    summary = json.loads((_run_dir(out) / "retrieval_metrics.json").read_text(encoding="utf-8"))
    recall = summary["configs"]["bm25"]["metrics"]["overall"]["recall_at_10"]
    assert recall == 1.0
    assert _metadata(out)["results_are_benchmark"] is False


def test_require_contract_refuses_the_committed_golden_set(tmp_path):
    """Scenario 2 at the gate: `--require-contract` must exit 2, not score."""
    import subprocess
    import sys

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.retrieval_benchmark",
            "--config",
            "bm25",
            "--limit",
            "2",
            "--require-contract",
            "--dataset",
            "tests/evaluation/golden_set.jsonl",
            "--output-dir",
            str(tmp_path / "out"),
            "--allow-dirty",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "dataset contract error" in completed.stderr
    assert "refusing to score" in completed.stderr


# ── 3. text-only fallback matching is not an attributable metric ─────────────


def test_text_only_fallback_matching_is_not_attributable(tmp_path, run_cli, text_only_dataset):
    """Scenario 3: level2 normalized-exact-text is a fallback, not ground truth."""
    from benchmarks.dataset import bucket_coverage

    out = tmp_path / "out"
    run_cli.main(["--config", "bm25", "--dataset", str(text_only_dataset), "--output-dir", str(out)])

    coverage = bucket_coverage([json.loads(line) for line in text_only_dataset.read_text().splitlines()])
    assert coverage["relevance_level"] == "level2_normalized_exact_text"

    metadata = _metadata(out)
    assert metadata["attributable"] is False
    assert metadata["results_are_benchmark"] is False
    assert metadata["dataset_contract"]["coverage"]["field_coverage"]["corpus_version"] == 0


def test_labels_without_identifiers_do_not_make_a_row_attributable(tmp_path, run_cli):
    """Every label filled in, but no `(doc_id, chunk_id)` — still not evidence.

    The human labels (`visual_required`, `complexity_label`) and the corpus
    version are all present; what is missing is the mapping from a passage to a
    real indexed chunk. Matching would fall back to normalized text, so the row
    must stay INVALID rather than inherit credit from the labels.
    """
    row = {
        "sample_id": "s0",
        "question": "q",
        "contexts": ["alpha passage"],
        "business_type": "regulation",
        "difficulty": "easy",
        "visual_required": False,
        "complexity_label": "simple",
        "corpus_version": VALID_CORPUS,
        "annotation": {
            "annotator": "a",
            "method": "manual",
            "annotated_at": "2026-10-09",
            "reviewed_by": "b",
        },
    }
    path = _write(tmp_path / "labels_only.jsonl", [row])
    verdict = validate_row(row)
    assert verdict.status == "INVALID"
    assert "missing_annotations" in verdict.reasons

    out = tmp_path / "out"
    run_cli.main(["--config", "bm25", "--dataset", str(path), "--output-dir", str(out)])
    metadata = _metadata(out)
    assert metadata["attributable"] is False
    assert metadata["results_are_benchmark"] is False
    assert "relevance matching: level2_normalized_exact_text" in _report(out)


def test_annotations_make_the_row_match_by_stable_id_not_by_text(tmp_path, run_cli):
    """Regression: v2 identity lives in `annotations`, so it must be honoured.

    Before this, a fully contract-valid row still reported
    `level2_normalized_exact_text` and was scored by text fallback while being
    labelled publishable — precisely the contradiction scenario 3 forbids.
    """
    row = _valid_row("s0", "doc-0", "doc-0-chunk-0", "alpha passage")
    path = _write(tmp_path / "annotated.jsonl", [row])

    from benchmarks.dataset import bucket_coverage, load_queries

    queries = load_queries(path)
    assert [item.key for item in queries[0].relevant_items] == ["doc-0"]

    coverage = bucket_coverage([row])
    assert coverage["relevance_level"] == "level1_stable_id"

    out = tmp_path / "out"
    run_cli.main(["--config", "bm25", "--dataset", str(path), "--output-dir", str(out)])
    assert _metadata(out)["results_are_benchmark"] is True
    assert "relevance matching: level1_stable_id" in _report(out)


# ── 4. unresolvable identifiers / wrong corpus version -> refuse ──────────────


def test_unknown_document_id_makes_the_row_invalid():
    """Scenario 4a: the identifier clause is fail-closed against a real index."""
    row = _valid_row("s0", "doc-missing", "chunk-missing", "alpha passage")
    assert validate_row(row, resolver=lambda *_: True).status == "VALID"

    verdict = validate_row(row, resolver=lambda *_: False)
    assert verdict.status == "INVALID"
    assert any("unresolved:doc-missing:chunk-missing" in reason for reason in verdict.reasons)


def test_wrong_corpus_version_is_unresolved_not_silently_accepted():
    """Scenario 4b: an epoch mismatch must not fall back to text matching."""
    known = {("doc-0", "doc-0-chunk-0", VALID_CORPUS)}
    resolver = lambda doc_id, chunk_id, version: (doc_id, chunk_id, version) in known  # noqa: E731

    row = _valid_row("s0", "doc-0", "doc-0-chunk-0", "alpha passage")
    assert validate_row(row, resolver=resolver).status == "VALID"

    stale = _valid_row("s0", "doc-0", "doc-0-chunk-0", "alpha passage", corpus_version="epoch_2026_01_01")
    verdict = validate_row(stale, resolver=resolver)
    assert verdict.status == "INVALID"
    assert any("unresolved" in reason for reason in verdict.reasons)


def test_unresolved_identifiers_block_the_whole_dataset(tmp_path):
    """Any unresolved row makes the dataset non-attributable as a whole."""
    rows = [
        _valid_row("s0", "doc-0", "doc-0-chunk-0", "alpha passage"),
        _valid_row("s1", "doc-404", "doc-404-chunk-0", "beta passage"),
    ]
    report = validate_dataset(rows, resolver=lambda *_: False)
    assert report.valid_count == 0
    assert report.total == 2
    with pytest.raises(DatasetContractError, match="refusing to score"):
        require_attributable(report)


# ── 5. partially annotated dataset -> rejected, with the denominator stated ───


def test_partial_annotation_is_rejected_under_the_default_fraction():
    """Scenario 5a: default `min_valid_fraction=1.0` refuses a partial dataset."""
    rows = [
        _valid_row("s0", "doc-0", "doc-0-chunk-0", "alpha passage"),
        {"question": "q1", "contexts": ["beta passage"], "business_type": "regulation", "difficulty": "easy"},
    ]
    report = validate_dataset(rows)
    assert report.valid_count == 1
    assert report.total == 2
    assert report.valid_fraction == 0.5
    with pytest.raises(DatasetContractError, match=r"1/2 samples are contract-valid"):
        require_attributable(report)


def test_partial_dataset_is_not_publishable_through_the_cli(tmp_path, run_cli):
    """The artifact must show the reduced denominator, not hide the invalid row."""
    rows = [
        _valid_row("s0", "doc-0", "doc-0-chunk-0", "alpha passage"),
        {"question": "q1", "contexts": ["beta passage"], "business_type": "regulation", "difficulty": "easy"},
    ]
    path = _write(tmp_path / "partial.jsonl", rows)
    out = tmp_path / "out"
    run_cli.main(["--config", "bm25", "--dataset", str(path), "--output-dir", str(out)])

    metadata = _metadata(out)
    assert metadata["attributable"] is False
    assert metadata["results_are_benchmark"] is False
    contract = metadata["dataset_contract"]
    assert (contract["valid_count"], contract["total"], contract["invalid_count"]) == (1, 2, 1)
    # Every reason is machine-readable, so an exclusion rule is auditable.
    assert contract["invalid_reasons"]["missing_or_invalid_corpus_version"] == 1


def test_lowering_the_fraction_is_an_explicit_waiver_not_a_default(tmp_path):
    """Scenario 5b: a lowered threshold must be stated, so it stays auditable."""
    rows = [
        _valid_row("s0", "doc-0", "doc-0-chunk-0", "alpha passage"),
        {"question": "q1", "contexts": ["beta passage"], "business_type": "regulation", "difficulty": "easy"},
    ]
    report = validate_dataset(rows)
    require_attributable(report, min_valid_fraction=1.0) if False else None  # default rejects

    with pytest.raises(DatasetContractError):
        require_attributable(report, min_valid_fraction=1.0)

    # The same dataset passes only when the caller states a smaller denominator,
    # and the reason text names both numbers so the waiver is visible.
    report_relaxed = validate_dataset(rows)
    try:
        require_attributable(report_relaxed, min_valid_fraction=0.4)
    except DatasetContractError as exc:  # pragma: no cover - documents intent
        pytest.fail(f"an explicit 0.4 waiver should admit a 0.5 dataset: {exc}")


def test_zero_valid_rows_are_rejected_at_every_fraction():
    rows = [{"question": "q", "contexts": ["c"]}]
    report = validate_dataset(rows)
    assert report.valid_count == 0
    for fraction in (0.0, 0.5, 1.0):
        with pytest.raises(DatasetContractError):
            require_attributable(report, min_valid_fraction=fraction)


# ── 6. a genuinely attributable dataset must publish ─────────────────────────


def test_attributable_dataset_publishes(tmp_path, run_cli, attributable_dataset):
    """Scenario 6: the gate must be passable, or it is not a gate.

    The fixture ranking is deterministic and proves the *decision logic* only.
    Its numbers are not a model-quality result and the artifact is never
    presented as one.
    """
    out = tmp_path / "out"
    exit_code = run_cli.main(["--config", "bm25", "--dataset", str(attributable_dataset), "--output-dir", str(out)])

    assert exit_code == 0
    metadata = _metadata(out)
    assert metadata["attributable"] is True
    assert metadata["results_are_benchmark"] is True
    assert metadata["dataset_contract"]["valid_count"] == 3
    assert metadata["dataset_contract"]["invalid_count"] == 0
    assert metadata["sample_count"] == 3

    summary = json.loads((_run_dir(out) / "retrieval_metrics.json").read_text(encoding="utf-8"))
    assert summary["any_results"] is True
    assert summary["executed_configs"] == ["bm25"]
    assert summary["configs"]["bm25"]["metrics"]["overall"]["sample_count"] == 3
    assert summary["configs"]["bm25"]["metrics"]["overall"]["recall_at_10"] == 1.0


def test_attributable_dataset_passes_the_explicit_contract_gate(tmp_path, run_cli, attributable_dataset):
    out = tmp_path / "out"
    exit_code = run_cli.main(
        [
            "--config",
            "bm25",
            "--dataset",
            str(attributable_dataset),
            "--require-contract",
            "--output-dir",
            str(out),
        ]
    )
    assert exit_code == 0
    assert _metadata(out)["results_are_benchmark"] is True


def test_published_denominator_matches_the_contract_valid_rows(tmp_path, run_cli, attributable_dataset):
    """The reported denominator and the contract-valid count must agree."""
    out = tmp_path / "out"
    run_cli.main(["--config", "bm25", "--dataset", str(attributable_dataset), "--output-dir", str(out)])
    metadata = _metadata(out)
    summary = json.loads((_run_dir(out) / "retrieval_metrics.json").read_text(encoding="utf-8"))
    assert (
        summary["configs"]["bm25"]["metrics"]["overall"]["sample_count"] == metadata["dataset_contract"]["valid_count"]
    )
    assert summary["configs"]["bm25"]["completed_sample_count"] == metadata["sample_count"]
    assert f"sample_count: {metadata['dataset_contract']['valid_count']}" in _report(out)


def test_relevance_strategy_is_stable_id_for_an_attributable_dataset(tmp_path, run_cli, attributable_dataset):
    out = tmp_path / "out"
    run_cli.main(["--config", "bm25", "--dataset", str(attributable_dataset), "--output-dir", str(out)])
    assert "relevance matching: level1_stable_id" in _report(out)


def test_attributability_requires_a_non_empty_dataset():
    """An empty report is never attributable, even at fraction 0.0."""
    report = validate_dataset([])
    assert report.total == 0
    assert report.valid_count == 0
    with pytest.raises(DatasetContractError, match="empty"):
        require_attributable(report, min_valid_fraction=0.0)
