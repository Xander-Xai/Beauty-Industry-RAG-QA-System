"""Query-side CLIP text-encoder contract.

Every other CLIP test substitutes the encoder, so nothing in the suite ever
executed the real ``EmbeddingService.encode_text_clip`` body. That gap is what
let the accessors it depends on stay undefined: the two properties were
missing from the class while ``__init__`` still initialised the private
attributes behind them, so the call raised ``AttributeError`` *before* any
model load. ``retrieval/parallel_recall.py`` catches that in its per-path
``except Exception`` and returns ``[]``, so the ``clip_visual`` recall path
silently contributed nothing on every request.

These tests drive the real methods and assert the class contract, using a stub
``transformers`` surface so no CLIP weights are required. They assert wiring
and shape, never model quality — real weights stay ``VAL-MODEL-002``/issue #8.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import patch

import numpy as np
import pytest
import torch

from models.embedding_service import EmbeddingService


class _StubProcessor:
    """Stands in for ``transformers.CLIPProcessor``.

    Returns the tokenizer-style batch dict the real processor returns, so
    ``encode_text_clip`` exercises its own code path unchanged.
    """

    def __init__(self, model_path):
        self.model_path = model_path

    @classmethod
    def from_pretrained(cls, model_path, **kwargs):
        return cls(model_path)

    def __call__(self, *, text, return_tensors, padding, **kwargs):
        assert return_tensors == "pt"
        assert padding is True
        return {"input_ids": torch.ones((len(text), 4), dtype=torch.long)}


class _StubCLIPModel:
    """Stands in for ``transformers.CLIPModel``; returns L2-normalizable rows."""

    def __init__(self, model_path):
        self.model_path = model_path
        self.eval_called = False

    @classmethod
    def from_pretrained(cls, model_path, **kwargs):
        return cls(model_path)

    def eval(self):
        self.eval_called = True
        return self

    def get_text_features(self, **inputs):
        batch = inputs["input_ids"].shape[0]
        base = torch.arange(1, 3, dtype=torch.float32)
        return torch.stack([base * (i + 1) for i in range(batch)])


@pytest.fixture
def stub_transformers():
    """Install a minimal ``transformers`` exposing only the CLIP classes used."""
    module = types.ModuleType("transformers")
    module.CLIPProcessor = _StubProcessor
    module.CLIPModel = _StubCLIPModel
    with patch.dict(sys.modules, {"transformers": module}):
        yield module


class TestClipEncoderAccessors:
    """The accessors are part of the class contract, not an implementation detail."""

    def test_accessors_are_defined_on_the_class(self):
        assert isinstance(EmbeddingService.clip_processor, property)
        assert isinstance(EmbeddingService.clip_model, property)

    def test_constructor_seeds_the_private_attributes(self):
        service = EmbeddingService()
        assert service._clip_model is None
        assert service._clip_processor is None

    def test_clip_path_defaults_to_the_configured_collection_model(self):
        from common.config import get_config_dict

        service = EmbeddingService()
        assert service._clip_model_path == get_config_dict()["embedding"]["image_clip"]["model_path"]

    def test_clip_path_is_independent_of_the_bge_override(self):
        """Passing a BGE override must not repoint the CLIP encoder at a text model."""
        service = EmbeddingService(model_path="/some/text/model")
        assert service._bge_model_path == "/some/text/model"
        assert service._clip_model_path != "/some/text/model"


class TestClipTextEncoding:
    """The real encoder bodies must run instead of raising AttributeError."""

    def test_encode_text_clip_returns_a_vector(self, stub_transformers):
        service = EmbeddingService(clip_model_path="/stub/clip")
        vector = service.encode_text_clip("视黄醇面霜配方")

        assert vector.shape == (2,)
        assert np.isfinite(vector).all()

    def test_encode_text_clip_puts_the_model_in_eval_mode(self, stub_transformers):
        service = EmbeddingService(clip_model_path="/stub/clip")
        service.encode_text_clip("成分表")

        assert service._clip_model.eval_called is True

    def test_encode_texts_clip_batch_returns_l2_normalized_rows(self, stub_transformers):
        service = EmbeddingService(clip_model_path="/stub/clip")
        matrix = service.encode_texts_clip_batch(["包装外观", "成分表", "备案号"])

        assert matrix.shape == (3, 2)
        norms = np.linalg.norm(matrix, axis=1)
        assert np.allclose(norms, 1.0, atol=1e-5)

    def test_loading_is_lazy_and_happens_once(self, stub_transformers):
        """Constructing the service must not pull weights; first use loads once."""
        service = EmbeddingService(clip_model_path="/stub/clip")
        assert service._clip_model is None

        service.encode_text_clip("第一次")
        loaded = service._clip_model
        assert loaded is not None

        service.encode_text_clip("第二次")
        assert service._clip_model is loaded


class TestClipRecallPathReachesTheEncoder:
    """The recall path must be able to obtain an embedding at all.

    ``retrieval.parallel_recall._recall_clip`` swallows any exception and
    returns ``[]``, which is how an ``AttributeError`` here became invisible.
    Only the Qdrant boundary is stubbed — the encoder, the service and the
    recall method are the real ones, so this fails if the accessors go missing
    again. Real recall quality still needs real weights and stays deferred to
    ``VAL-MODEL-002`` / issue #8.
    """

    def test_recall_clip_reaches_the_real_encoder(self, stub_transformers):
        from retrieval.parallel_recall import ParallelRecallManager

        manager = ParallelRecallManager()
        clip_retriever = manager.clip_retriever
        assert clip_retriever is not None, "clip_retriever must be wired for the visual path"

        captured: dict = {}

        def _fake_search(clip_embedding, qdrant_filter=None, top_k=20):
            captured["embedding"] = np.asarray(clip_embedding)
            captured["top_k"] = top_k
            return [{"doc_id": "d1", "content": "c", "score": 0.9}]

        with patch.object(clip_retriever, "search", _fake_search):
            hits = manager._recall_clip("包装外观", None, 5)

        # The encoder ran and produced a real vector before the Qdrant call.
        assert captured["embedding"].shape == (2,)
        assert captured["top_k"] == 5
        assert [h.doc_id for h in hits] == ["d1"]
        assert hits[0].source == "clip_visual"
