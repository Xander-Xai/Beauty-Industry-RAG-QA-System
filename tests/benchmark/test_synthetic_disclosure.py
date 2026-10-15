"""Every artifact that quotes dataset text must state that dataset's class.

The fixtures name real regulation instruments next to invented limits (see
``DATA_PROVENANCE.md`` §4). A sidecar does not travel with a forwarded file, so
the disclosure has to be inside each artifact that carries the text.

These tests read the real writers rather than re-describing them, because the
failure being guarded is a writer that quietly stopped emitting the field — or,
worse, one that emits a claim it did not verify.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.models import STATUS_EXECUTED, ConfigOutcome, QueryBenchmarkResult
from benchmarks.provenance import (
    SYNTHETIC_DATA_CLASS,
    SYNTHETIC_DATA_NOTICE,
    UNCLASSIFIED_DATA_CLASS,
    dataset_data_provenance,
    declared_data_class,
)
from benchmarks.runner import write_per_query_jsonl
from tests.evaluation.ragas_report import EvaluationReport

GOLDEN_SET = "tests/evaluation/golden_set.jsonl"


def test_notice_names_the_two_things_a_reader_needs():
    """Fiction, and what the content must not be used for."""
    assert "虚构" in SYNTHETIC_DATA_NOTICE
    assert "SYNTHETIC" in SYNTHETIC_DATA_NOTICE
    for forbidden_use in ("compliance", "ingredient-limit"):
        assert forbidden_use in SYNTHETIC_DATA_NOTICE


# --- the class is derived, never assumed ----------------------------------
#
# `--dataset` accepts any JSONL. A hardcoded SYNTHETIC claim would become a
# false statement the moment someone points the benchmark at a licensed corpus.


def test_declared_fixture_resolves_to_synthetic(tmp_path: Path) -> None:
    assert declared_data_class(GOLDEN_SET) == (SYNTHETIC_DATA_CLASS, False)

    block = dataset_data_provenance(GOLDEN_SET)

    assert block == {
        "data_class": SYNTHETIC_DATA_CLASS,
        "carries_real_regulatory_claims": False,
        "notice": SYNTHETIC_DATA_NOTICE,
    }


def test_undeclared_dataset_is_unclassified_not_synthetic(tmp_path: Path) -> None:
    """An operator's own corpus gets no claim in either direction."""
    foreign = tmp_path / "licensed.jsonl"
    foreign.write_text('{"question": "x"}\n', encoding="utf-8")

    block = dataset_data_provenance(foreign)

    assert block["data_class"] == UNCLASSIFIED_DATA_CLASS
    assert block["carries_real_regulatory_claims"] is None
    assert block["notice"] == ""


def test_real_regulatory_claims_are_never_asserted_for_unclassified_data(tmp_path: Path) -> None:
    """The one claim that must never be made blind is the reassuring one."""
    foreign = tmp_path / "licensed.jsonl"
    foreign.write_text('{"question": "x"}\n', encoding="utf-8")

    assert dataset_data_provenance(foreign)["carries_real_regulatory_claims"] is not False


# --- the writers actually emit it -----------------------------------------


def test_per_query_row_carries_the_callers_dataset_class(tmp_path: Path) -> None:
    """`per_query_results.jsonl` quotes the dataset `question` verbatim.

    A row is the unit that gets forwarded, so the row itself has to say what it
    is. This is the artifact most likely to be pasted into an issue or a slide.
    """
    row = _write_one_row(tmp_path, dataset_data_provenance(GOLDEN_SET))

    assert row["data_class"] == SYNTHETIC_DATA_CLASS
    assert row["carries_real_regulatory_claims"] is False
    assert row["notice"] == SYNTHETIC_DATA_NOTICE
    # The disclosure is additive: the scoring fields the reports consume are
    # untouched, so an artifact written before this change still compares.
    assert row["sample_id"] == "0000"
    assert row["first_relevant_rank"] == 1


def test_per_query_row_carries_an_unclassified_dataset_unchanged(tmp_path: Path) -> None:
    """The row must not claim a class the caller did not establish."""
    unclassified = {"data_class": UNCLASSIFIED_DATA_CLASS, "carries_real_regulatory_claims": None, "notice": ""}

    row = _write_one_row(tmp_path, unclassified)

    assert row["data_class"] == UNCLASSIFIED_DATA_CLASS
    assert "notice" in row and row["notice"] == ""


def test_benchmark_markdown_report_leads_with_the_notice() -> None:
    """`report.md` is the most human-shareable artifact of the three."""
    from benchmarks.report import build_report

    markdown = build_report(
        metadata=dataset_data_provenance(GOLDEN_SET),
        environment={},
        summary={},
        coverage={"sample_count": 0, "available_buckets": []},
        per_query_rows=0,
        synthetic_retriever=False,
    )

    lines = [line for line in markdown.splitlines() if line.strip()]
    assert "虚构" in lines[1]


def test_benchmark_markdown_report_does_not_invent_a_class() -> None:
    from benchmarks.report import build_report

    markdown = build_report(
        metadata=dataset_data_provenance("/tmp/somebody-elses-corpus.jsonl"),
        environment={},
        summary={},
        coverage={"sample_count": 0, "available_buckets": []},
        per_query_rows=0,
        synthetic_retriever=False,
    )

    assert "虚构" not in markdown
    assert "UNCLASSIFIED" in markdown


def test_ragas_report_defaults_stay_loadable_and_stay_unknown():
    """A legacy report has no provenance, and must not acquire one by default.

    Defaulting to "synthetic, no real regulatory claims" would turn an absence
    into an affirmative statement about a dataset this process never looked at.
    """
    legacy_payload = {
        "tag": "baseline",
        "timestamp": "2026-01-01T00:00:00",
        "overall_scores": {},
        "per_sample_scores": [],
        "category_breakdown": {},
        "dataset_size": 0,
        "metadata": {},
    }

    legacy = EvaluationReport(**legacy_payload)

    assert legacy.data_class == UNCLASSIFIED_DATA_CLASS
    assert legacy.carries_real_regulatory_claims is None
    assert legacy.notice == ""
    # Still loadable: the defaults exist so an old file does not raise TypeError.
    assert EvaluationReport(**json.loads(json.dumps(legacy_payload))).tag == "baseline"


def test_ragas_report_markdown_leads_with_the_notice() -> None:
    """The notice has to be above the numbers, not in a footer."""
    from tests.evaluation.ragas_report import RAGASReporter

    report = EvaluationReport(
        tag="baseline",
        timestamp="2026-01-01T00:00:00",
        overall_scores={"faithfulness": 0.5},
        per_sample_scores=[],
        category_breakdown={},
        dataset_size=0,
        metadata={},
        **dataset_data_provenance(GOLDEN_SET),
    )

    markdown = RAGASReporter.format_report_markdown(RAGASReporter.__new__(RAGASReporter), report)

    lines = [line for line in markdown.splitlines() if line.strip()]
    notice_line = next(i for i, line in enumerate(lines) if "虚构" in line)
    faithfulness_line = next(i for i, line in enumerate(lines) if "Faithfulness" in line and "|" in line)
    assert notice_line < faithfulness_line
    assert "SYNTHETIC" in lines[2]


def test_ragas_report_markdown_does_not_invent_a_class_it_does_not_have() -> None:
    """An unknown dataset gets no classification line at all."""
    from tests.evaluation.ragas_report import RAGASReporter

    report = EvaluationReport(
        tag="baseline",
        timestamp="2026-01-01T00:00:00",
        overall_scores={},
        per_sample_scores=[],
        category_breakdown={},
        dataset_size=0,
        metadata={},
        **dataset_data_provenance("/tmp/somebody-elses-corpus.jsonl"),
    )

    markdown = RAGASReporter.format_report_markdown(RAGASReporter.__new__(RAGASReporter), report)

    assert "数据类别" not in markdown
    assert "虚构" not in markdown


# --- helpers ---------------------------------------------------------------


def _write_one_row(tmp_path: Path, data_provenance: dict) -> dict:
    from benchmarks.runner import ConfigRun

    result = QueryBenchmarkResult(
        sample_id="0000",
        question="化妆品中铅含量的限量标准是什么？",
        business_type="regulation",
        difficulty="easy",
        config="bm25",
        relevant_keys=("d1::c0",),
        retrieved_keys=("d1::c0",),
        first_relevant_rank=1,
        recall_at={1: 1.0},
        hit_at={1: True},
        reciprocal_rank=1.0,
        ndcg_at_10=1.0,
        latency_ms={},
    )
    run = ConfigRun(outcome=ConfigOutcome(config_name="bm25", status=STATUS_EXECUTED), results=[result])

    path = tmp_path / "per_query_results.jsonl"
    assert write_per_query_jsonl(path, [run], data_provenance) == 1
    return json.loads(path.read_text(encoding="utf-8").strip())
