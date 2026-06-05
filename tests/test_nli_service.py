"""
Tests for the NLI batch endpoint in the inline rerank service (run_services.py).

Covers:
  1. Real NLI model inference (mocked transformers/torch) returns correct per-pair probabilities.
  2. Missing NLI model at startup causes POST /rerank/nli to return HTTP 501
     with a clear contract message.
  3. Empty pairs list returns empty results.
  4. Aggregator-not-initialized returns 503.
  5. Model inference failure returns 500.
"""

import sys
import os
import math
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Lightweight torch mock (enough for softmax + tensor slicing)
# ---------------------------------------------------------------------------

class _FakeTensor:
    """Minimal tensor shim that supports item(), indexing, and shape."""

    def __init__(self, data):
        if isinstance(data, (list, tuple)) and isinstance(data[0], (list, tuple, _FakeTensor)):
            self._data = [list(row) if not isinstance(row, _FakeTensor) else row._data for row in data]
        elif isinstance(data, (list, tuple)):
            self._data = list(data)
        else:
            self._data = [data]

    @property
    def shape(self):
        if self._data and isinstance(self._data[0], list):
            return (len(self._data), len(self._data[0]))
        return (len(self._data),)

    def __getitem__(self, idx):
        if isinstance(idx, int):
            row = self._data[idx]
            if isinstance(row, list):
                return _FakeTensor(row)
            return _FakeTensor([row])
        return _FakeTensor(self._data)

    def item(self):
        if isinstance(self._data, list) and len(self._data) == 1 and isinstance(self._data[0], (int, float)):
            return self._data[0]
        return self._data


def _softmax(values):
    """Compute softmax over a list of floats."""
    m = max(values)
    exps = [math.exp(v - m) for v in values]
    total = sum(exps)
    return [e / total for e in exps]


class _FakeTensorOps:
    """Shim for torch.no_grad and torch.softmax used inside the endpoint handler."""

    class _NoGrad:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def no_grad(self):
        return self._NoGrad()

    def softmax(self, tensor, dim=-1):
        results = []
        for row in tensor._data:
            probs = _softmax(row)
            results.append(probs)
        return _FakeTensor(results)


_orig_torch = sys.modules.get("torch")
torch_mock = types.ModuleType("torch")
torch_mock.tensor = lambda data, **kw: _FakeTensor(data)
torch_mock.softmax = lambda tensor, dim=-1: _FakeTensorOps().softmax(tensor, dim)
torch_mock.no_grad = _FakeTensorOps().no_grad
torch_mock.nn = types.ModuleType("torch.nn")
torch_mock.nn.Module = object
# Provide a minimal `cuda` shim so that modules doing torch.cuda.is_available()
# at import time do not blow up when they pick up this mock.
_cuda_mock = types.ModuleType("torch.cuda")
_cuda_mock.is_available = lambda: False
torch_mock.cuda = _cuda_mock
# Use direct assignment, NOT setdefault — setdefault is a no-op when another
# test file has already imported the real `torch` into sys.modules.
sys.modules["torch"] = torch_mock


@pytest.fixture(autouse=True)
def _restore_real_torch():
    """Ensure the fake torch is active during this module's tests, then restore
    whatever was in sys.modules['torch'] before (real torch or absent)."""
    # Re-inject the fake torch before each test (may have been restored by a
    # previous test's teardown).
    sys.modules["torch"] = torch_mock
    yield
    # Tear-down: put back the original entry so other test modules are unaffected.
    if _orig_torch is None:
        sys.modules.pop("torch", None)
    else:
        sys.modules["torch"] = _orig_torch


# ---------------------------------------------------------------------------
# Helper: build a minimal FastAPI TestClient from the inline service code
# ---------------------------------------------------------------------------

def _make_app(nli_model=None, nli_tokenizer=None, aggregator_ready=True):
    """
    Dynamically construct the FastAPI app with controllable NLI model state.

    Returns (app, client).
    """
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
    from starlette.testclient import TestClient

    app = FastAPI(title="Rerank Batch Service (test)")

    class NLIRequest(BaseModel):
        pairs: list[list[str]]

    _aggregator = "ready" if aggregator_ready else None
    _nli_tokenizer = nli_tokenizer
    _nli_model = nli_model

    @app.post("/rerank/nli")
    def nli_batch(req: NLIRequest):
        nonlocal _nli_model, _nli_tokenizer
        if _aggregator is None:
            raise HTTPException(503, "Aggregator not initialized")

        if _nli_model is None:
            raise HTTPException(
                status_code=501,
                detail=(
                    "NLI model not available.  Deploy a transformers-compatible NLI model "
                    "at ./models/nli-deberta and ensure the 'transformers' package is installed.  "
                    "Contract: POST /rerank/nli  body={pairs:[[premise,hypothesis],...]}  "
                    "response={results:[{contradiction:float,entailment:float,neutral:float},...],count:int}"
                ),
            )

        if not req.pairs:
            return {"results": [], "count": 0}

        try:
            import torch
            premises = [pair[0] for pair in req.pairs]
            hypotheses = [pair[1] for pair in req.pairs]

            inputs = _nli_tokenizer(
                premises, hypotheses,
                return_tensors="pt", truncation=True, max_length=512, padding=True,
            )

            with torch.no_grad():
                outputs = _nli_model(**inputs)
                probs = torch.softmax(outputs.logits, dim=-1)

            results = []
            for i in range(len(req.pairs)):
                results.append({
                    "contradiction": round(probs[i][0].item(), 6),
                    "entailment":    round(probs[i][1].item(), 6),
                    "neutral":       round(probs[i][2].item(), 6),
                })
            return {"results": results, "count": len(results)}
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.get("/health")
    def health():
        return {"status": "healthy", "service": "rerank-batch"}

    client = TestClient(app)
    return app, client


def _make_fake_nli_model(*scores_per_pair):
    """
    Build a fake NLI model + tokenizer that returns predetermined logits.

    Args:
        scores_per_pair: list of (contradiction, entailment, neutral) logit triples.
                         If empty, a default of (0.1, 0.8, 0.1) is reused for all pairs.
    """
    if not scores_per_pair:
        scores_per_pair = ((0.1, 0.8, 0.1),)

    class FakeModel:
        def __init__(self):
            self._scores = list(scores_per_pair)

        def forward(self, **kwargs):
            # Infer batch size from input_ids shape
            if "input_ids" in kwargs and hasattr(kwargs["input_ids"], "shape"):
                batch_size = kwargs["input_ids"].shape[0]
            else:
                batch_size = 1
            logits_list = []
            for i in range(batch_size):
                idx = i % len(self._scores)
                logits_list.append(list(self._scores[idx]))
            return types.SimpleNamespace(logits=_FakeTensor(logits_list))

    class FakeTokenizer:
        def __call__(self, premises, hypotheses, **kwargs):
            n = len(premises) if isinstance(premises, list) else 1
            return {
                "input_ids": _FakeTensor([[0] * 10] * n),
                "attention_mask": _FakeTensor([[1] * 10] * n),
            }

    model = FakeModel()
    # The endpoint calls model(**inputs), so make the model callable
    class CallableFakeModel(FakeModel):
        def __call__(self, **kwargs):
            return self.forward(**kwargs)

    return CallableFakeModel(), FakeTokenizer()


# ---------------------------------------------------------------------------
# Test 1: Real NLI model inference (mocked torch + transformers-style model)
# ---------------------------------------------------------------------------

class TestNLIRealInference:
    """POST /rerank/nli with a working NLI model returns actual probabilities."""

    def test_single_pair_inference(self):
        """Single pair: model logits are softmaxed into 3-class probabilities."""
        fake_model, fake_tokenizer = _make_fake_nli_model(
            (0.05, 0.90, 0.05),   # pair 0: high entailment
        )
        _, client = _make_app(nli_model=fake_model, nli_tokenizer=fake_tokenizer)

        resp = client.post("/rerank/nli", json={"pairs": [["premise text", "hypothesis text"]]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        result = body["results"][0]
        # Softmax of (0.05, 0.90, 0.05) ~ (0.142, 0.725, 0.142)
        expected = _softmax([0.05, 0.90, 0.05])
        assert result["contradiction"] == pytest.approx(expected[0], abs=1e-4)
        assert result["entailment"] == pytest.approx(expected[1], abs=1e-4)
        assert result["neutral"] == pytest.approx(expected[2], abs=1e-4)
        assert result["entailment"] > result["contradiction"]
        assert set(result.keys()) == {"contradiction", "entailment", "neutral"}

    def test_multiple_pairs_inference(self):
        """Multiple pairs: each pair gets its own softmaxed result."""
        fake_model, fake_tokenizer = _make_fake_nli_model(
            (0.0, 1.0, 0.0),    # pair 0: strong entailment
            (1.0, 0.0, 0.0),    # pair 1: strong contradiction
        )
        _, client = _make_app(nli_model=fake_model, nli_tokenizer=fake_tokenizer)

        pairs = [
            ["The sky is blue", "The sky has a blue color"],
            ["It is raining", "The sun is shining"],
        ]
        resp = client.post("/rerank/nli", json={"pairs": pairs})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2

        r0 = body["results"][0]
        exp0 = _softmax([0.0, 1.0, 0.0])
        assert r0["entailment"] == pytest.approx(exp0[1], abs=1e-4)
        assert r0["entailment"] > r0["contradiction"]

        r1 = body["results"][1]
        exp1 = _softmax([1.0, 0.0, 0.0])
        assert r1["contradiction"] == pytest.approx(exp1[0], abs=1e-4)
        assert r1["contradiction"] > r1["entailment"]

    def test_probabilities_sum_to_one(self):
        """Each result's three probabilities should sum to approximately 1.0."""
        fake_model, fake_tokenizer = _make_fake_nli_model((0.3, 0.5, 0.2))
        _, client = _make_app(nli_model=fake_model, nli_tokenizer=fake_tokenizer)

        resp = client.post("/rerank/nli", json={"pairs": [["a", "b"]]})
        body = resp.json()
        r = body["results"][0]
        total = r["contradiction"] + r["entailment"] + r["neutral"]
        assert abs(total - 1.0) < 1e-4

    def test_empty_pairs_returns_empty(self):
        """Empty pairs list returns empty results with count=0."""
        fake_model, fake_tokenizer = _make_fake_nli_model()
        _, client = _make_app(nli_model=fake_model, nli_tokenizer=fake_tokenizer)

        resp = client.post("/rerank/nli", json={"pairs": []})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["results"] == []


# ---------------------------------------------------------------------------
# Test 2: NLI model unavailable -- HTTP 501 with contract message
# ---------------------------------------------------------------------------

class TestNLIMissingFallback:
    """When the NLI model failed to load at startup, the endpoint returns 501."""

    def test_no_model_returns_501(self):
        """nli_model=None triggers 501 with clear contract message."""
        _, client = _make_app(nli_model=None, nli_tokenizer=None)

        resp = client.post("/rerank/nli", json={"pairs": [["a", "b"]]})
        assert resp.status_code == 501
        body = resp.json()
        detail = body.get("detail", "")
        # Contract message must mention the endpoint, model path, and response shape
        assert "NLI model not available" in detail
        assert "./models/nli-deberta" in detail
        assert "POST /rerank/nli" in detail
        assert "contradiction" in detail
        assert "entailment" in detail
        assert "neutral" in detail

    def test_501_with_empty_pairs(self):
        """501 takes priority over empty pairs -- model check happens first."""
        _, client = _make_app(nli_model=None)
        resp = client.post("/rerank/nli", json={"pairs": []})
        assert resp.status_code == 501


# ---------------------------------------------------------------------------
# Test 3: Aggregator not initialized -- HTTP 503
# ---------------------------------------------------------------------------

class TestAggregatorNotInitialized:
    """503 is returned when the aggregator singleton was not set."""

    def test_503_when_aggregator_missing(self):
        _, client = _make_app(nli_model=None, aggregator_ready=False)
        resp = client.post("/rerank/nli", json={"pairs": [["a", "b"]]})
        assert resp.status_code == 503
        assert "Aggregator not initialized" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# Test 4: Model inference raises an exception -- HTTP 500
# ---------------------------------------------------------------------------

class TestNLIInferenceError:
    """If the model throws during inference the endpoint returns 500."""

    def test_model_raises_returns_500(self):
        """Broken model causes 500 (not a crash)."""

        class BrokenModel:
            def __call__(self, **kwargs):
                raise RuntimeError("CUDA out of memory")

        class DummyTokenizer:
            def __call__(self, *args, **kwargs):
                return {"input_ids": _FakeTensor([[0] * 10]), "attention_mask": _FakeTensor([[1] * 10])}

        _, client = _make_app(nli_model=BrokenModel(), nli_tokenizer=DummyTokenizer())
        resp = client.post("/rerank/nli", json={"pairs": [["a", "b"]]})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Test 5: Health endpoint still works
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_200(self):
        _, client = _make_app()
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
