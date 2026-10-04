"""Regression tests for query-aware RRF weight propagation.

`core/pipeline.py` passes `rrf_weights` to
`ParallelRecallManager.execute()`, but the callee signature never accepted it.
The caller/callee mismatch raised `TypeError` on the online retrieval mainline
and 2000+ green tests could not detect it, because:

* `tests/test_architecture_contract.py` exercises `_build_rrf_weights()` in
  isolation and never calls `execute()`;
* `tests/test_pipeline_ordering.py` mocks `pipeline._parallel_recall.execute`,
  and a mock accepts any keyword.

These tests therefore drive the **real** `execute()`. Only the leaf retrievers
and the `rrf_fusion` call are substituted; `execute()` itself is never mocked.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _manager():
    """A ParallelRecallManager wired to fakes, following the repo's test pattern."""
    from retrieval.parallel_recall import ParallelRecallManager

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._dense_retriever = None
    manager._bm25_retriever = None
    manager._clip_retriever = None
    manager._rewrite_variants_retriever = None
    manager.max_workers = 2
    return manager


def _top_k(**overrides):
    paths = {
        "dense_bge": {"enabled": True, "top_k": 5},
        "bm25_es": {"enabled": False, "top_k": 0},
        "clip_visual": {"enabled": False, "top_k": 0},
        "rewrite_variants": {"enabled": False, "top_k": 0},
    }
    paths.update(overrides)
    return paths


def _run(manager, **kwargs):
    defaults = {
        "query": "法规限量查询",
        "query_embedding": np.array([0.1]),
        "user_role_mask": 0,
        "user_dept_mask": 0,
        "use_clip": False,
        "clip_top_k": 0,
        "top_k_per_path": _top_k(),
    }
    defaults.update(kwargs)
    return manager.execute(**defaults)


# ---------------------------------------------------------------------------
# Test A — signature / runtime contract
# ---------------------------------------------------------------------------


def test_execute_accepts_rrf_weights():
    """The real execute() must accept the keyword the pipeline passes.

    On the unfixed tree this raises
    `TypeError: execute() got an unexpected keyword argument 'rrf_weights'`.
    """
    assert "rrf_weights" in inspect.signature(_manager().execute).parameters, (
        "execute() does not accept rrf_weights; the pipeline caller cannot work"
    )


def test_execute_with_rrf_weights_does_not_raise_type_error():
    """End-to-end runtime contract through the real execute() path."""
    manager = _manager()
    manager._recall_dense = lambda *args, **kwargs: []

    results, agreement = _run(
        manager,
        rrf_weights={"dense_bge": 1.0, "bm25_es": 1.5, "clip_visual": 2.0, "rewrite_variant": 1.0},
    )

    assert results == []
    assert isinstance(agreement, float)


def test_pipeline_caller_keywords_are_all_accepted_by_execute():
    """Guard the whole caller/callee contract, not just this one keyword.

    Parses the real call site in `core/pipeline.py` and asserts every keyword it
    passes exists in the real signature. This is the check whose absence let the
    P0 ship: a mocked `execute()` accepts anything.
    """
    from retrieval.parallel_recall import ParallelRecallManager

    tree = ast.parse((ROOT / "core" / "pipeline.py").read_text(encoding="utf-8"))
    keywords: set[str] = set()
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "execute"):
            continue
        if not (isinstance(func.value, ast.Attribute) and func.value.attr == "parallel_recall"):
            continue
        found = True
        keywords.update(kw.arg for kw in node.keywords if kw.arg)

    assert found, "could not locate the parallel_recall.execute() call in core/pipeline.py"
    assert "rrf_weights" in keywords, "core/pipeline.py no longer passes rrf_weights"

    accepted = set(inspect.signature(ParallelRecallManager.execute).parameters)
    unexpected = keywords - accepted
    assert not unexpected, (
        f"core/pipeline.py passes keyword(s) {sorted(unexpected)} that "
        f"ParallelRecallManager.execute() does not accept; accepted={sorted(accepted)}"
    )


# ---------------------------------------------------------------------------
# Test B — weight propagation
# ---------------------------------------------------------------------------


def _capture_rrf_fusion(monkeypatch):
    """Replace only the leaf rrf_fusion call and record the weights it receives."""
    import retrieval.parallel_recall as module

    captured: dict = {}

    def fake_fusion(results_map, k=60, weights=None):
        captured["k"] = k
        captured["weights"] = weights
        return []

    monkeypatch.setattr(module, "rrf_fusion", fake_fusion)
    return captured


def test_query_aware_weights_reach_rrf_fusion(monkeypatch):
    """The dynamic weights must arrive at rrf_fusion, not the static config ones."""
    import json

    captured = _capture_rrf_fusion(monkeypatch)
    manager = _manager()
    manager._recall_dense = lambda *args, **kwargs: []

    dynamic = {"dense_bge": 1.0, "bm25_es": 1.5, "clip_visual": 2.0, "rewrite_variant": 1.0}
    _run(manager, rrf_weights=dynamic)

    assert captured["weights"] == dynamic, (
        f"rrf_fusion received {captured['weights']!r}, expected the query-aware weights {dynamic!r}"
    )

    static = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["retrieval"]["rrf"]["weights"]
    assert captured["weights"] != static, (
        "static config weights were forwarded; the query-aware override is being discarded"
    )


def test_rrf_k_constant_still_comes_from_config(monkeypatch):
    """The fix must not disturb the RRF k constant."""
    import json

    captured = _capture_rrf_fusion(monkeypatch)
    manager = _manager()
    manager._recall_dense = lambda *args, **kwargs: []

    expected_k = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["retrieval"]["rrf"]["k"]
    _run(manager, rrf_weights={"dense_bge": 1.0})

    assert captured["k"] == expected_k


# ---------------------------------------------------------------------------
# Test C — backwards-compatible fallback
# ---------------------------------------------------------------------------


def test_none_falls_back_to_static_config_weights(monkeypatch):
    """Omitting the parameter must preserve existing behaviour for other callers.

    `retrieval-service/main.py` calls execute() without rrf_weights, and every
    other call site in the repository does the same.
    """
    import json

    captured = _capture_rrf_fusion(monkeypatch)
    manager = _manager()
    manager._recall_dense = lambda *args, **kwargs: []

    static = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["retrieval"]["rrf"]["weights"]

    # Omitted entirely: identical to the pre-fix behaviour, which always read the
    # static config weights.
    _run(manager)
    assert captured["weights"] == static, "omitting rrf_weights must resolve to the static config weights"

    # Explicit None must behave exactly the same as omitting it.
    _run(manager, rrf_weights=None)
    assert captured["weights"] == static, "rrf_weights=None must resolve to the static config weights"

    # And neither may be confused with the explicit empty dict.
    _run(manager, rrf_weights={})
    assert captured["weights"] == {}, "an explicit {} must not be treated as absent"


def test_positional_callers_are_unaffected():
    """Existing positional call sites keep working unchanged."""
    manager = _manager()
    manager._recall_dense = lambda *args, **kwargs: []

    results, agreement = manager.execute(
        "query",
        np.array([0.1]),
        0,
        0,
        False,
        0,
        _top_k(),
    )

    assert results == []
    assert isinstance(agreement, float)


# ---------------------------------------------------------------------------
# Empty-dict semantics: why this fix uses `is not None` and not `or`
# ---------------------------------------------------------------------------


def test_empty_dict_means_equal_weights_not_config_weights(monkeypatch):
    """An explicit {} is a real value under the rrf_fusion contract.

    rrf_fusion documents "None 或空 dict 时等权重（1.0）", so {} means equal
    weighting. Using `or` here would silently substitute the static config
    weights (which carry the w_ocr=0.8 discount) and contradict the caller.
    """
    import json

    captured = _capture_rrf_fusion(monkeypatch)
    manager = _manager()
    manager._recall_dense = lambda *args, **kwargs: []

    _run(manager, rrf_weights={})

    static = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["retrieval"]["rrf"]["weights"]
    assert captured["weights"] == {}, "an explicit empty dict must be forwarded as-is"
    assert captured["weights"] != static, (
        "the empty dict was replaced by the static config weights; `is not None` is required"
    )


def test_pipeline_builder_never_returns_empty_weights():
    """Guards the assumption behind the `or` vs `is not None` choice.

    _build_rrf_weights always populates all four path keys, so the production
    caller never hits the ambiguous case. If that ever changes, the fallback
    semantics need to be revisited deliberately.
    """
    from core.pipeline import OnlineRAGPipeline

    pipeline = OnlineRAGPipeline.__new__(OnlineRAGPipeline)
    for business_type in ("regulation", "general", "research"):
        for visual in (True, False):
            weights = pipeline._build_rrf_weights(
                business_type=business_type,
                is_visual_relevant=visual,
            )
            assert weights, f"empty weights for {business_type}/{visual}"
            assert set(weights) == {
                "dense_bge",
                "bm25_es",
                "clip_visual",
                "rewrite_variant",
            }


@pytest.mark.parametrize(
    ("business_type", "visual", "path", "expected"),
    [
        ("regulation", False, "bm25_es", 1.5),
        ("regulation", False, "clip_visual", 1.0),
        ("general", True, "clip_visual", 2.0),
        ("general", False, "clip_visual", 1.0),
    ],
)
def test_builder_boosts_reach_fusion_through_execute(monkeypatch, business_type, visual, path, expected):
    """The boosts _build_rrf_weights computes must be the ones rrf_fusion sees."""
    from core.pipeline import OnlineRAGPipeline

    captured = _capture_rrf_fusion(monkeypatch)
    manager = _manager()
    manager._recall_dense = lambda *args, **kwargs: []

    pipeline = OnlineRAGPipeline.__new__(OnlineRAGPipeline)
    weights = pipeline._build_rrf_weights(
        business_type=business_type,
        is_visual_relevant=visual,
    )
    _run(manager, rrf_weights=weights)

    assert captured["weights"][path] == expected
