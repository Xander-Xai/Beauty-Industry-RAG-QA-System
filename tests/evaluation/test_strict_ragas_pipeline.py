from types import SimpleNamespace

import pytest

from tests.evaluation.ragas_eval import (
    RAGASEvaluator,
    _parse_args,
    complete_pipeline_report_errors,
    sample_id_for,
)


def report():
    return SimpleNamespace(
        mode="pipeline",
        evaluator_status="available",
        requested_samples=2,
        successful_samples=2,
        failed_samples=0,
        skipped_samples=0,
        failures=[],
        sample_ids=["a", "b"],
        per_sample_scores=[
            {"sample_id": "a", "faithfulness": 0.0},
            {"sample_id": "b", "faithfulness": 0.8},
        ],
        overall_scores={"faithfulness": 0.4},
    )


def test_strict_report_gate_accepts_measured_zero_and_complete_rows():
    assert complete_pipeline_report_errors(report(), ["faithfulness"]) == []


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("failed_samples", 1, "incomplete_pipeline_samples"),
        ("successful_samples", 1, "incomplete_pipeline_samples"),
        ("sample_ids", ["a", "a"], "incomplete_sample_identity"),
        ("sample_ids", ["b", "a"], "incomplete_sample_identity"),
        ("overall_scores", {"faithfulness": float("inf")}, "incomplete_metric_observations"),
        ("per_sample_scores", [{"sample_id": "a"}, {"sample_id": "b"}], "incomplete_metric_observations"),
    ],
)
def test_strict_report_gate_rejects_incomplete_evidence(field, value, reason):
    candidate = report()
    setattr(candidate, field, value)
    assert reason in complete_pipeline_report_errors(candidate, ["faithfulness"])


def test_sample_ids_keep_original_dataset_index(tmp_path):
    dataset = tmp_path / "data.jsonl"
    dataset.write_text(
        '{"question":"a","answer":"a","contexts":[],"ground_truth":"a"}\n'
        '{"question":"b","answer":"b","contexts":[],"ground_truth":"b"}\n',
        encoding="utf-8",
    )
    evaluator = RAGASEvaluator(str(dataset))
    original_id = sample_id_for(evaluator.dataset[1], 1)
    selected = evaluator.select_entries(sample_ids=[original_id])
    assert evaluator._entry_sample_id(selected[0], 0) == original_id


def test_strict_cli_refuses_reference_mode_and_duplicate_metrics():
    with pytest.raises(SystemExit):
        _parse_args(["--require-complete-pipeline"])
    with pytest.raises(SystemExit):
        _parse_args(
            [
                "--pipeline",
                "--require-ragas",
                "--require-complete-pipeline",
                "--metrics",
                "faithfulness",
                "faithfulness",
            ]
        )
