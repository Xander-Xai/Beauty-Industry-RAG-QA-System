"""Real evaluator validation for RAGAS.

These tests call a real RAGAS evaluator with a real provider API key. They are
skipped by default so normal CI never depends on an external key or spend.

Level 2 (evaluator smoke): a small synthetic set exercises the real evaluator,
metrics and report serialization. It uses the dataset's reference answers and is
explicitly NOT a project-pipeline quality result.

Level 3 (real pipeline RAGAS) is intentionally not stubbed here: it requires the
project's VLLM/Qdrant/ES/Redis infrastructure. When that is available, run:

    RUN_REAL_RAGAS=1 OPENAI_API_KEY=... \
      python -m tests.evaluation.ragas_eval --require-ragas --pipeline --limit 15

Gate: RUN_REAL_RAGAS=1 and OPENAI_API_KEY set.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from tests.evaluation.ragas_eval import RAGASEvaluator
from tests.evaluation.ragas_report import RAGASReporter

pytestmark = [pytest.mark.integration, pytest.mark.external_api, pytest.mark.runtime]

ROOT = Path(__file__).resolve().parents[2]


def _enabled() -> bool:
    return os.environ.get("RUN_REAL_RAGAS") == "1" and bool(os.environ.get("OPENAI_API_KEY"))


def _synthetic_dataset() -> str:
    entries = [
        {
            "question": "化妆品的保质期一般是多久？",
            "answer": "未开封通常 3 年，开封后 6-12 个月。",
            "contexts": ["化妆品保质期通常标注在包装上，开封后会缩短。"],
            "ground_truth": "未开封化妆品一般 3 年，开封后建议 6-12 个月内使用完毕。",
            "business_type": "general",
            "difficulty": "easy",
        },
        {
            "question": "敏感肌是什么意思？",
            "answer": "皮肤对外界刺激反应过度的状态。",
            "contexts": ["敏感肌是皮肤的一种状态，不是固定皮肤类型。"],
            "ground_truth": "敏感肌指皮肤对外界刺激反应过度，并非一种固定皮肤类型。",
            "business_type": "ingredient",
            "difficulty": "easy",
        },
        {
            "question": "成分表中的防腐剂有什么作用？",
            "answer": "抑制微生物生长，延长产品保质期。",
            "contexts": ["防腐剂用于抑制微生物滋生，保证产品在使用期内稳定。"],
            "ground_truth": "防腐剂抑制微生物生长，延长化妆品保质期并保证使用安全。",
            "business_type": "ingredient",
            "difficulty": "medium",
        },
    ]
    handle = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
    for entry in entries:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    handle.close()
    return handle.name


@pytest.mark.skipif(not _enabled(), reason="set RUN_REAL_RAGAS=1 and OPENAI_API_KEY")
def test_real_evaluator_smoke_and_report(monkeypatch):
    """Real evaluator: at least one metric returns, no fallback, report serializes."""
    dataset_path = _synthetic_dataset()
    try:
        evaluator = RAGASEvaluator(dataset_path)
        scores = evaluator.evaluate(metrics=["faithfulness", "answer_relevancy"])
        assert evaluator.last_available, "real evaluator must be available"
        assert "_warning" not in scores
        assert any(isinstance(v, (int, float)) for v in scores.values())

        report = RAGASReporter(evaluator).build_report(tag="real-smoke")
        assert report.evaluator_status == "available"
        assert report.evaluator_model
        assert report.successful_samples == 3
        assert report.metrics
        # token/key must never appear in the serialized report
        blob = json.dumps(report.__dict__, ensure_ascii=False, default=str)
        assert "sk-" not in blob
    finally:
        os.unlink(dataset_path)
