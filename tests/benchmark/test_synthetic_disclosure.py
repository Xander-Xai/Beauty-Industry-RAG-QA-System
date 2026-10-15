"""Every artifact that quotes evaluation-fixture text must identify itself.

The fixtures name real regulation instruments next to invented limits (see
``DATA_PROVENANCE.md`` §4). A sidecar does not travel with a forwarded file, so
the disclosure has to be inside each artifact that carries the text. These tests
are the regression for that property: they read the real writers rather than
re-describing them, because the failure mode being guarded is a writer that
quietly stopped emitting the field.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.models import STATUS_EXECUTED, ConfigOutcome, QueryBenchmarkResult
from benchmarks.provenance import SYNTHETIC_DATA_CLASS, SYNTHETIC_DATA_NOTICE, synthetic_data_provenance
from benchmarks.runner import write_per_query_jsonl
from tests.evaluation.ragas_report import EvaluationReport


def test_notice_names_the_two_things_a_reader_needs():
    """Fiction, and what the content must not be used for."""
    assert "虚构" in SYNTHETIC_DATA_NOTICE
    assert "SYNTHETIC" in SYNTHETIC_DATA_NOTICE
    for forbidden_use in ("compliance", "ingredient-limit"):
        assert forbidden_use in SYNTHETIC_DATA_NOTICE


def test_provenance_block_declares_fabrication_not_real_claims():
    block = synthetic_data_provenance()

    assert block["data_class"] == SYNTHETIC_DATA_CLASS
    assert block["carries_real_regulatory_claims"] is False
    assert block["notice"] == SYNTHETIC_DATA_NOTICE


def test_per_query_row_carries_the_disclosure(tmp_path: Path) -> None:
    """`per_query_results.jsonl` quotes the fixture `question` verbatim.

    A row is the unit that gets forwarded, so the row itself has to say what it
    is. This is the artifact most likely to be pasted into an issue or a slide.
    """
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
    run = ConfigRun(outcome=_executed_outcome(), results=(result,))

    path = tmp_path / "per_query_results.jsonl"
    assert write_per_query_jsonl(path, (run,)) == 1

    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["data_class"] == SYNTHETIC_DATA_CLASS
    assert row["carries_real_regulatory_claims"] is False
    assert row["notice"] == SYNTHETIC_DATA_NOTICE
    # The disclosure is additive: the scoring fields the reports consume are
    # untouched, so an artifact written before this change still compares.
    assert row["sample_id"] == "0000"
    assert row["first_relevant_rank"] == 1


def test_ragas_report_defaults_stay_loadable_and_declare_themselves():
    """Old report files must keep loading; new ones must declare themselves."""
    # A report written before the fields existed has neither key.
    legacy = EvaluationReport(
        tag="baseline",
        timestamp="2026-01-01T00:00:00",
        overall_scores={},
        per_sample_scores=[],
        category_breakdown={},
        dataset_size=0,
        metadata={},
    )
    assert legacy.data_class == SYNTHETIC_DATA_CLASS
    assert legacy.carries_real_regulatory_claims is False
    assert (
        EvaluationReport(
            **json.loads(
                json.dumps(
                    {
                        "tag": "t",
                        "timestamp": "x",
                        "overall_scores": {},
                        "per_sample_scores": [],
                        "category_breakdown": {},
                        "dataset_size": 0,
                        "metadata": {},
                    }
                )
            )
        ).notice
        == ""
    )


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
        **synthetic_data_provenance(),
    )

    markdown = RAGASReporter.format_report_markdown(RAGASReporter.__new__(RAGASReporter), report)

    lines = [line for line in markdown.splitlines() if line.strip()]
    notice_line = next(i for i, line in enumerate(lines) if "虚构" in line)
    faithfulness_line = next(i for i, line in enumerate(lines) if "Faithfulness" in line and "|" in line)
    assert notice_line < faithfulness_line


def _executed_outcome():
    """Minimal executed ConfigOutcome so write_per_query_jsonl emits the row."""
    return ConfigOutcome(config_name="bm25", status=STATUS_EXECUTED)
