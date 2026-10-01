"""Production and deterministic test embedders for the offline pipeline.

The BGE text adapter and the deterministic text embedder are reused from the
Phase 1 slice to keep one embedding contract. This module adds the CLIP image
adapter (512d) plus a deterministic image embedder for tests. No model is
loaded at import time and no network call happens on import.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

from offline.text_ingestion import BGETextEmbedder, DeterministicTestEmbedder

__all__ = [
    "BGETextEmbedder",
    "CLIPImageEmbedder",
    "DeterministicTestEmbedder",
    "DeterministicTestImageEmbedder",
    "ImageEmbedder",
    "validate_vectors",
]


class ImageEmbedder(Protocol):
    dimension: int
    embedding_version: str

    def embed_images(self, images: list[bytes]) -> list[list[float]]: ...


def validate_vectors(vectors: list[list[float]], expected_count: int, dimension: int) -> None:
    if len(vectors) != expected_count:
        raise ValueError(f"embedder returned {len(vectors)} vectors for {expected_count} inputs")
    for index, vector in enumerate(vectors):
        if len(vector) != dimension or any(not math.isfinite(float(value)) for value in vector):
            raise ValueError(f"embedding {index} must contain {dimension} finite values")


class CLIPImageEmbedder:
    """Production CLIP image encoder matching the online CLIP preprocessing.

    Uses ``CLIPProcessor`` + ``CLIPModel.get_image_features`` and L2-normalizes
    the output, mirroring ``EmbeddingService.encode_texts_clip_batch`` so that
    offline image vectors and online query vectors share one contract.
    """

    embedding_type = "image_clip"

    def __init__(
        self,
        model_name_or_path: str,
        dimension: int = 512,
        batch_size: int = 16,
        model_revision: str | None = None,
        device: str | None = None,
    ):
        if not model_name_or_path:
            raise ValueError("a configured CLIP model name or path is required")
        if type(dimension) is not int or dimension <= 0:
            raise ValueError("dimension must be a positive integer")
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        self.model_name_or_path = model_name_or_path
        self.dimension = dimension
        self.batch_size = batch_size
        self.device = device
        self.embedding_version = f"{model_revision or model_name_or_path}:clip-image-v1"
        self._processor = None
        self._model = None

    def _load(self):
        if self._model is not None:
            return
        try:
            from transformers import CLIPModel, CLIPProcessor

            self._processor = CLIPProcessor.from_pretrained(self.model_name_or_path)
            self._model = CLIPModel.from_pretrained(self.model_name_or_path)
            self._model.eval()
            if self.device:
                self._model.to(self.device)
        except Exception as exc:
            raise RuntimeError(f"failed to initialize CLIP model {self.model_name_or_path!r}: {exc}") from exc

    def embed_images(self, images: list[bytes]) -> list[list[float]]:
        if not images:
            return []
        import io

        import torch
        from PIL import Image

        self._load()
        rows: list[list[float]] = []
        for offset in range(0, len(images), self.batch_size):
            batch = images[offset : offset + self.batch_size]
            try:
                pil_images = [Image.open(io.BytesIO(data)).convert("RGB") for data in batch]
                inputs = self._processor(images=pil_images, return_tensors="pt")
                if self.device:
                    inputs = {key: value.to(self.device) for key, value in inputs.items()}
                with torch.no_grad():
                    features = self._model.get_image_features(**inputs)
                features = features / features.norm(dim=-1, keepdim=True)
                rows.extend(features.cpu().numpy().tolist())
            except Exception as exc:
                raise RuntimeError(f"CLIP image embedding failed for {self.model_name_or_path!r}: {exc}") from exc
        validate_vectors(rows, len(images), self.dimension)
        return rows


class DeterministicTestImageEmbedder:
    """Stable byte-hash image embedder for tests; this is not CLIP."""

    embedding_type = "image_clip"

    def __init__(self, dimension: int = 512, embedding_version: str = "test-clip-image-v1"):
        if type(dimension) is not int or dimension <= 0:
            raise ValueError("dimension must be a positive integer")
        self.dimension = dimension
        self.embedding_version = embedding_version

    def embed_images(self, images: list[bytes]) -> list[list[float]]:
        rows = []
        for data in images:
            digest = hashlib.blake2b(data, digest_size=32).digest()
            vector = [0.0] * self.dimension
            for index, byte in enumerate(digest):
                vector[index % self.dimension] += (byte + 1) / 256.0
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
            rows.append(vector)
        validate_vectors(rows, len(images), self.dimension)
        return rows
