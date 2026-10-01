"""Shared fixtures for evaluation tests.

The ``fake_ragas`` fixture installs a deterministic in-memory RAGAS + datasets
stand-in so the evaluator's "available" path can be tested without network or
the real (currently unimportable) ragas stack. It records every evaluate call so
tests can assert the evaluator runs exactly once per run.
"""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture
def fake_ragas(monkeypatch):
    call_log: dict = {"calls": 0, "datasets": [], "metrics": []}

    mock_ragas = types.ModuleType("ragas")
    mock_metrics = types.ModuleType("ragas.metrics")

    class MockMetric:
        pass

    for name in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
        setattr(mock_metrics, name, MockMetric())

    def mock_evaluate(dataset, metrics):
        call_log["calls"] += 1
        call_log["datasets"].append(dataset)
        call_log["metrics"].append(metrics)
        return type(
            "Result",
            (),
            {
                "faithfulness": 0.80,
                "answer_relevancy": 0.90,
                "context_precision": 0.70,
                "context_recall": 0.85,
            },
        )()

    mock_ragas.evaluate = mock_evaluate

    mock_datasets = types.ModuleType("datasets")

    class MockDataset:
        @classmethod
        def from_dict(cls, data):
            return data

    mock_datasets.Dataset = MockDataset

    monkeypatch.setitem(sys.modules, "ragas", mock_ragas)
    monkeypatch.setitem(sys.modules, "ragas.metrics", mock_metrics)
    monkeypatch.setitem(sys.modules, "datasets", mock_datasets)
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")

    return call_log
