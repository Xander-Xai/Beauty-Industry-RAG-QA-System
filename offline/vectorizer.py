"""Embedding facade used by the snapshot builder.

Centralizes the text/image embedding calls so writers and the pipeline share
one embedding contract per epoch.
"""

from __future__ import annotations


class Vectorizer:
    def __init__(self, text_embedder, image_embedder):
        self.text_embedder = text_embedder
        self.image_embedder = image_embedder

    @property
    def text_embedding_version(self) -> str:
        return getattr(self.text_embedder, "embedding_version", "unspecified-v1")

    @property
    def image_embedding_version(self) -> str:
        return getattr(self.image_embedder, "embedding_version", "unspecified-image-v1")

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self.text_embedder.embed_texts(texts)

    def embed_images(self, images: list[bytes]) -> list[list[float]]:
        if not images:
            return []
        return self.image_embedder.embed_images(images)
