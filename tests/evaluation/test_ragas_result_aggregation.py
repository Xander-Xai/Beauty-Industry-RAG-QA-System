"""Offline contract for RAGAS EvaluationResult row aggregation."""

from types import SimpleNamespace

import pytest

from tests.evaluation.ragas_eval import aggregate_ragas_scores


def test_real_shape_rows_are_aggregated_without_scalar_attributes():
    raw = SimpleNamespace(scores=[{"faithfulness": 0.0}, {"faithfulness": 1.0}])
    assert aggregate_ragas_scores(raw, ["faithfulness", "context_recall"], 2) == {"faithfulness": 0.5}


def test_missing_and_nonfinite_observations_are_never_zero_filled():
    raw = SimpleNamespace(scores=[{"faithfulness": 0.0}, {"faithfulness": float("nan")}, {}])
    assert aggregate_ragas_scores(raw, ["faithfulness", "context_recall"], 3) == {"faithfulness": 0.0}


@pytest.mark.parametrize("rows", [[], [{"faithfulness": 1.0}], [None, {}]])
def test_wrong_row_population_is_not_a_measured_result(rows):
    raw = SimpleNamespace(scores=rows, faithfulness=0.9)
    assert aggregate_ragas_scores(raw, ["faithfulness"], 2) == {}


def test_public_dataframe_api_supports_result_without_list_scores():
    class Frame:
        def to_dict(self, *, orient):
            assert orient == "records"
            return [{"faithfulness": 0.2}, {"faithfulness": 0.6}]

    raw = SimpleNamespace(to_pandas=lambda: Frame())
    assert aggregate_ragas_scores(raw, ["faithfulness"], 2) == {"faithfulness": pytest.approx(0.4)}


def test_legacy_scalar_fallback_remains_available():
    raw = SimpleNamespace(faithfulness=0.7, context_recall=True)
    assert aggregate_ragas_scores(raw, ["faithfulness", "context_recall"], 2) == {"faithfulness": 0.7}
