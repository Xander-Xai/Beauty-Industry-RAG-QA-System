"""OCR and image extraction pipeline with pluggable providers.

OCR and CLIP models are injected as providers, so unit tests never download
models. Production providers (PaddleOCR, CLIP) are imported lazily.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from offline.chunking import POINT_NAMESPACE
from offline.document_processor import ExtractedImage
from offline.validation import validate_epoch, validate_permissions

SUPPORTED_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")


@dataclass(frozen=True)
class OCRBlock:
    text: str
    confidence: float
    bbox: tuple[float, float, float, float]
    page_number: int | None = None
    image_index: int = 0
    font_size: float | None = None
    is_center: bool = False
    is_large: bool = False


@dataclass(frozen=True)
class OCRResult:
    full_text: str
    blocks: list[OCRBlock]
    main_text: str = ""


class OCRProvider(Protocol):
    def extract(self, image_bytes: bytes, *, page_number: int | None = None, image_index: int = 0) -> OCRResult: ...


@dataclass(frozen=True)
class ProcessedImage:
    doc_id: str
    image_id: str
    image_index: int
    page_number: int | None
    source_path: str
    image_uri: str
    ocr_full_text: str
    ocr_main_text: str
    ocr_blocks: list[dict]
    embedding: list[float]
    embedding_type: str
    embedding_version: str
    role_mask: int
    dept_mask: int
    status: str
    doc_version_epoch: str
    metadata: dict = field(default_factory=dict)
    #: Canonical ingestion provenance, persisted verbatim onto the point. An
    #: image point is the retrievable representation of the same source as its
    #: text chunks, so it carries the same trust decision.
    provenance: dict = field(default_factory=dict)


def image_identity(doc_id: str, content_hash: str, image_index: int) -> str:
    """Stable logical image id for a standalone image source."""
    return str(uuid.uuid5(POINT_NAMESPACE, f"{doc_id}:image:{content_hash}:{image_index}"))


def _is_center(bbox: tuple[float, float, float, float], width: float, height: float) -> bool:
    x0, y0, x1, y1 = bbox
    center_x = (x0 + x1) / 2
    center_y = (y0 + y1) / 2
    return 0.2 * width < center_x < 0.8 * width and 0.2 * height < center_y < 0.8 * height


class PaddleOCRProvider:
    """Optional production OCR provider wrapping PaddleOCR."""

    def __init__(self, language: str = "ch", large_font_threshold: float = 20.0):
        self.language = language
        self.large_font_threshold = large_font_threshold
        self._model = None

    def _load(self):
        if self._model is not None:
            return
        try:
            from paddleocr import PaddleOCR

            self._model = PaddleOCR(use_angle_cls=True, lang=self.language, show_log=False)
        except Exception as exc:
            raise RuntimeError(f"failed to initialize PaddleOCR: {exc}") from exc

    def extract(self, image_bytes: bytes, *, page_number: int | None = None, image_index: int = 0) -> OCRResult:
        import io

        import numpy as np
        from PIL import Image

        self._load()
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        width, height = image.size
        try:
            result = self._model.ocr(np.array(image), cls=True)
        except Exception as exc:
            raise RuntimeError(f"PaddleOCR extraction failed: {exc}") from exc
        blocks: list[OCRBlock] = []
        lines = result[0] if result else None
        for line in lines or []:
            bbox_points = line[0]
            text = str(line[1][0])
            confidence = float(line[1][1])
            xs = [float(point[0]) for point in bbox_points]
            ys = [float(point[1]) for point in bbox_points]
            bbox = (min(xs), min(ys), max(xs), max(ys))
            font_size = bbox[3] - bbox[1]
            blocks.append(
                OCRBlock(
                    text=text,
                    confidence=confidence,
                    bbox=bbox,
                    page_number=page_number,
                    image_index=image_index,
                    font_size=font_size,
                    is_center=_is_center(bbox, width, height),
                    is_large=font_size > self.large_font_threshold,
                )
            )
        return OCRResult(
            full_text=" ".join(block.text for block in blocks),
            blocks=blocks,
        )


class DeterministicTestOCRProvider:
    """Stable OCR provider for tests; no model is loaded."""

    def __init__(
        self,
        text: str | None = None,
        confidence: float = 0.95,
        font_size: float = 30.0,
        is_center: bool = True,
        is_large: bool = True,
    ):
        self.text = text
        self.confidence = confidence
        self.font_size = font_size
        self.is_center = is_center
        self.is_large = is_large

    def extract(self, image_bytes: bytes, *, page_number: int | None = None, image_index: int = 0) -> OCRResult:
        text = self.text if self.text is not None else f"ocr-{hashlib.sha256(image_bytes).hexdigest()[:12]}"
        block = OCRBlock(
            text=text,
            confidence=self.confidence,
            bbox=(10.0, 10.0, 90.0, 40.0),
            page_number=page_number,
            image_index=image_index,
            font_size=self.font_size,
            is_center=self.is_center,
            is_large=self.is_large,
        )
        return OCRResult(full_text=text, blocks=[block])


class ImageProcessor:
    """Run OCR + image embedding and build authorization-aware image records."""

    def __init__(
        self,
        ocr_provider: OCRProvider,
        image_embedder,
        visual_weight_repeat: int = 3,
        min_confidence: float = 0.7,
    ):
        if type(visual_weight_repeat) is not int or visual_weight_repeat <= 0:
            raise ValueError("visual_weight_repeat must be a positive integer")
        self.ocr_provider = ocr_provider
        self.image_embedder = image_embedder
        self.visual_weight_repeat = visual_weight_repeat
        self.min_confidence = min_confidence

    def apply_visual_weights(self, blocks: list[OCRBlock]) -> str:
        """Deterministically repeat core-region text to raise its recall weight."""
        parts: list[str] = []
        for block in blocks:
            is_core = block.is_center and block.is_large and block.confidence >= self.min_confidence
            if is_core:
                parts.extend([block.text] * self.visual_weight_repeat)
            else:
                parts.append(block.text)
        return " ".join(part for part in parts if part)

    def process_image(
        self,
        *,
        image: ExtractedImage,
        doc_id: str,
        role_mask: int,
        dept_mask: int,
        doc_version_epoch: str,
        source_path: str,
        image_uri: str | None = None,
        extra_metadata: dict | None = None,
        provenance: dict | None = None,
    ) -> ProcessedImage:
        validate_permissions(role_mask, dept_mask)
        validate_epoch(doc_version_epoch)
        ocr = self.ocr_provider.extract(
            image.data,
            page_number=image.page_number,
            image_index=image.image_index,
        )
        main_text = self.apply_visual_weights(ocr.blocks) or ocr.full_text
        vectors = self.image_embedder.embed_images([image.data])
        if not vectors:
            raise ValueError(f"image embedder returned no vector for {image.image_id}")
        embedding = vectors[0]
        dimension = getattr(self.image_embedder, "dimension", len(embedding))
        if len(embedding) != dimension:
            raise ValueError(f"image embedding must contain {dimension} values")
        return ProcessedImage(
            doc_id=doc_id,
            image_id=image.image_id,
            image_index=image.image_index,
            page_number=image.page_number,
            source_path=source_path,
            image_uri=image_uri if image_uri is not None else f"{source_path}#image={image.image_index}",
            ocr_full_text=ocr.full_text,
            ocr_main_text=main_text,
            ocr_blocks=[asdict(block) for block in ocr.blocks],
            embedding=embedding,
            embedding_type=getattr(self.image_embedder, "embedding_type", "image_clip"),
            embedding_version=getattr(self.image_embedder, "embedding_version", "unspecified-image-v1"),
            role_mask=role_mask,
            dept_mask=dept_mask,
            status="active",
            doc_version_epoch=doc_version_epoch,
            metadata={
                **(extra_metadata or {}),
                **image.metadata,
                "page_number": image.page_number,
                "image_index": image.image_index,
            },
            provenance=dict(provenance or {}),
        )

    def process_standalone_image(
        self,
        source: str | Path,
        *,
        role_mask: int,
        dept_mask: int,
        doc_version_epoch: str,
        doc_id: str,
        image_index: int = 0,
        provenance: dict | None = None,
    ) -> ProcessedImage:
        path = Path(source)
        if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ValueError(f"unsupported image type {path.suffix or '<none>'}")
        data = path.read_bytes()
        content_hash = hashlib.sha256(data).hexdigest()
        extracted = ExtractedImage(
            image_id=image_identity(doc_id, content_hash, image_index),
            data=data,
            mime_type=f"image/{path.suffix.lower().lstrip('.')}",
            page_number=None,
            image_index=image_index,
            metadata={"format": "image"},
        )
        return self.process_image(
            image=extracted,
            doc_id=doc_id,
            role_mask=role_mask,
            dept_mask=dept_mask,
            doc_version_epoch=doc_version_epoch,
            source_path=str(path.resolve()),
            provenance=provenance,
        )
