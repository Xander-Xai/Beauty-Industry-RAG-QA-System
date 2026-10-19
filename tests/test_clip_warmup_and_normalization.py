"""CLIP query-encoder warm-up and normalisation contract (Issue #102).

Two engineering gaps closed here, both provable without real CLIP weights:

1. **Cold-start.** The shared query-side encoder (``ParallelRecallManager
   .clip_embedding_service``) loads lazily, so the first multimodal request after every
   process start paid the weight load inside ``clip_sync.timeout_ms`` and returned no
   visual hits. ``EmbeddingService.warm_up_clip`` / ``ParallelRecallManager.warm_up_clip``
   now perform that load at start-up and record a ``READY`` / ``DEGRADED`` state on a
   health surface, without making a missing model fatal.
2. **Asymmetric normalisation.** ``encode_text_clip`` returned an unnormalised vector
   while ``encode_texts_clip_batch`` L2-normalised. Both now return unit vectors.

This file asserts wiring and contract only. It does **not** claim real CLIP recall quality
or a real-weight timing figure: that stays ``PENDING`` under ``VAL-MODEL-002`` / issue #8.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models.embedding_service import EmbeddingService  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


class _StubProcessor:
    """Minimal CLIP processor: one fixed-width token batch, content-independent."""

    def __init__(self, model_path):
        self.model_path = model_path

    @classmethod
    def from_pretrained(cls, model_path, **kwargs):
        return cls(model_path)

    def __call__(self, *, text, return_tensors, padding, **kwargs):
        return {"input_ids": torch.ones((len(text), 4), dtype=torch.long)}


class _StubCLIPModel:
    """Returns finite, L2-normalisable rows so the normalisation path is exercised."""

    def __init__(self, model_path):
        self.model_path = model_path

    @classmethod
    def from_pretrained(cls, model_path, **kwargs):
        return cls(model_path)

    def eval(self):
        return self

    def get_text_features(self, **inputs):
        batch = inputs["input_ids"].shape[0]
        base = torch.arange(1, 3, dtype=torch.float32)
        return torch.stack([base * (i + 1) for i in range(batch)])


@pytest.fixture
def stub_transformers():
    module = types.ModuleType("transformers")
    module.CLIPProcessor = _StubProcessor
    module.CLIPModel = _StubCLIPModel
    with patch.dict(sys.modules, {"transformers": module}):
        yield module


# ── one normalisation contract for both entry points ────────────────────────


class TestNormalisationContractIsUniform:
    def test_single_encode_returns_a_unit_vector(self, stub_transformers):
        service = EmbeddingService(clip_model_path="/stub/clip")

        vector = service.encode_text_clip("视黄醇面霜配方")

        assert np.isclose(np.linalg.norm(vector), 1.0, atol=1e-5)

    def test_single_and_batch_agree_for_the_same_text(self, stub_transformers):
        service = EmbeddingService(clip_model_path="/stub/clip")

        single = service.encode_text_clip("包装外观")
        batch = service.encode_texts_clip_batch(["包装外观"])[0]

        assert np.allclose(single, batch, atol=1e-6)
        assert np.isclose(np.linalg.norm(single), 1.0, atol=1e-5)

    def test_batch_rows_are_unit_vectors(self, stub_transformers):
        service = EmbeddingService(clip_model_path="/stub/clip")

        matrix = service.encode_texts_clip_batch(["a", "b", "c"])

        assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-5)

    def test_a_positively_scaled_embedding_is_normalised_away(self, stub_transformers):
        """A bigger raw vector must not produce a longer query: the contract is direction."""

        class _ScaledModel(_StubCLIPModel):
            def get_text_features(self, **inputs):
                return super().get_text_features(**inputs) * 1000.0

        module = types.ModuleType("transformers")
        module.CLIPProcessor = _StubProcessor
        module.CLIPModel = _ScaledModel
        with patch.dict(sys.modules, {"transformers": module}):
            service = EmbeddingService(clip_model_path="/stub/clip")
            vector = service.encode_text_clip("包装外观")

        assert np.isclose(np.linalg.norm(vector), 1.0, atol=1e-5)


# ── warm-up: success, and the two non-fatal failure modes ───────────────────


class TestWarmUp:
    def test_warm_up_reports_ready_and_loads_weights_once(self, stub_transformers):
        service = EmbeddingService(clip_model_path="/stub/clip")
        assert service._clip_model is None

        ok, detail = service.warm_up_clip()

        assert (ok, detail) == (True, "")
        loaded = service._clip_model
        assert loaded is not None

        # A later request reuses the loaded model; it does not reload.
        service.encode_text_clip("again")
        assert service._clip_model is loaded

    def test_missing_weights_is_reported_not_raised(self):
        class _Missing:
            @classmethod
            def from_pretrained(cls, *args, **kwargs):
                raise FileNotFoundError("no such model directory")

        module = types.ModuleType("transformers")
        module.CLIPProcessor = _Missing
        module.CLIPModel = _Missing
        with patch.dict(sys.modules, {"transformers": module}):
            service = EmbeddingService(clip_model_path="/does/not/exist")
            ok, detail = service.warm_up_clip()

        assert ok is False
        assert detail == "missing_weights"

    def test_missing_transformers_is_reported_not_raised(self):
        service = EmbeddingService(clip_model_path="/stub/clip")
        # A ``transformers`` module without the CLIP classes stands in for "not installed".
        with patch.dict(sys.modules, {"transformers": types.ModuleType("transformers")}):
            ok, detail = service.warm_up_clip()

        assert ok is False
        assert detail == "not_installed"

    def test_a_loader_error_is_a_bounded_token_not_a_message(self):
        class _Boom:
            @classmethod
            def from_pretrained(cls, *args, **kwargs):
                raise RuntimeError("CUDA out of memory on /home/user/secret")

        module = types.ModuleType("transformers")
        module.CLIPProcessor = _Boom
        module.CLIPModel = _Boom
        with patch.dict(sys.modules, {"transformers": module}):
            service = EmbeddingService(clip_model_path="/stub/clip")
            ok, detail = service.warm_up_clip()

        assert ok is False
        assert detail == "loader_error"
        assert "secret" not in detail and "/home" not in detail

    def test_non_finite_weights_are_refused(self):
        class _NonFiniteModel(_StubCLIPModel):
            def get_text_features(self, **inputs):
                batch = inputs["input_ids"].shape[0]
                return torch.full((batch, 2), float("inf"))

        module = types.ModuleType("transformers")
        module.CLIPProcessor = _StubProcessor
        module.CLIPModel = _NonFiniteModel
        with patch.dict(sys.modules, {"transformers": module}):
            service = EmbeddingService(clip_model_path="/stub/clip")
            ok, detail = service.warm_up_clip()

        assert (ok, detail) == (False, "non_finite_vector")


# ── manager-level warm-up and status surface ────────────────────────────────


class _FakeEncoder:
    def __init__(self, ok=True, detail=""):
        self.ok = ok
        self.detail = detail
        self.calls = 0

    def warm_up_clip(self):
        self.calls += 1
        return self.ok, self.detail


class TestManagerWarmUpStatus:
    def _manager(self, encoder):
        from retrieval.parallel_recall import ParallelRecallManager

        manager = ParallelRecallManager()
        manager._clip_embedding_service = encoder
        return manager

    def test_status_is_pending_before_warm_up(self):
        manager = self._manager(_FakeEncoder())

        assert manager.clip_warmup_status() == {"state": "PENDING", "detail": ""}

    def test_warm_up_delegates_to_the_shared_encoder_and_reports_ready(self):
        encoder = _FakeEncoder(True, "")
        manager = self._manager(encoder)

        ok, detail = manager.warm_up_clip()

        assert (ok, detail) == (True, "")
        assert encoder.calls == 1
        assert manager.clip_warmup_status() == {"state": "READY", "detail": ""}

    def test_warm_up_records_the_degraded_state_without_raising(self):
        manager = self._manager(_FakeEncoder(False, "missing_weights"))

        ok, detail = manager.warm_up_clip()

        assert (ok, detail) == (False, "missing_weights")
        assert manager.clip_warmup_status() == {"state": "DEGRADED", "detail": "missing_weights"}

    def test_degraded_warm_up_does_not_stop_the_recall_manager(self):
        """A process without CLIP weights still serves the other recall paths."""
        manager = self._manager(_FakeEncoder(False, "missing_weights"))

        manager.warm_up_clip()

        assert manager.clip_warmup_status()["state"] == "DEGRADED"


class _RecordingEncoder:
    """Counts encodes so the async path's encoder choice is observable."""

    def __init__(self):
        self.encoded: list[str] = []

    def encode_text_clip(self, text):
        self.encoded.append(text)
        return np.ones(512, dtype=np.float32)


class _FakeClipRetriever:
    def search(self, embedding, qdrant_filter, top_k):
        return []


def test_async_clip_reuses_the_shared_warmable_encoder(monkeypatch):
    """``recall_async_clip`` must not cold-load a second CLIP encoder.

    The sync path was fixed to reuse the manager-scoped warmable encoder (#99/#102);
    the async path still constructed a fresh ``EmbeddingService`` per call, which
    reloads the CLIP weights inside the background 0.5s budget and holds a second
    copy of the model in memory.
    """
    from retrieval import parallel_recall
    from retrieval.parallel_recall import ParallelRecallManager

    manager = ParallelRecallManager()
    encoder = _RecordingEncoder()
    manager._clip_embedding_service = encoder
    manager._clip_retriever = _FakeClipRetriever()

    monkeypatch.setitem(parallel_recall.config, "clip_async", {"enabled": True})

    def _forbidden(*args, **kwargs):
        raise AssertionError("the async path constructed a fresh EmbeddingService")

    with patch("models.embedding_service.EmbeddingService", _forbidden):
        results = manager.recall_async_clip("q", None, session=None, user_role_mask=1, user_dept_mask=1)

    assert results == []
    assert encoder.encoded == ["q"], "the shared warmable encoder was not used"


# ── start-up wiring on the two entry points ─────────────────────────────────


def test_online_app_and_service_warm_up_at_startup():
    app_source = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
    service_source = (REPO_ROOT / "retrieval-service" / "main.py").read_text(encoding="utf-8")

    assert "warm_up_clip" in app_source, "the online app must warm the encoder at start-up"
    assert "warm_up_clip" in service_source
    assert "lifespan=lifespan" in service_source


def test_retrieval_service_health_reports_the_clip_warm_up_state():
    """Start the deployed service in-process (subprocess) and read /health.

    Run in a subprocess for the same reason ``test_microservice_imports`` does: importing
    ``retrieval-service.main`` chdirs and edits ``sys.path``, which must not leak into the
    test process. No real CLIP weights are required — a missing model must yield
    ``DEGRADED`` with a bounded detail token, not a crash.
    """
    probe = r"""
import importlib, json, sys
from fastapi.testclient import TestClient

module = importlib.import_module("retrieval-service.main")
with TestClient(module.app) as client:
    response = client.get("/health")
    body = response.json()
print(json.dumps({"status_code": response.status_code, "clip": body.get("clip")}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert payload["status_code"] == 200
    assert payload["clip"]["state"] in {"READY", "DEGRADED"}
    if payload["clip"]["state"] == "DEGRADED":
        assert payload["clip"]["detail"] in {"not_installed", "missing_weights", "loader_error", "warmup_error"}
