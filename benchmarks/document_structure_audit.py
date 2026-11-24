"""Conservative structural audit for parsed PDF/DOCX/XLSX documents.

An audit checks identity, page/row coverage and OCR routing continuity.
It does NOT evaluate OCR text, page layout accuracy, or retrieval relevance.
The optional expected_pdf_pages must come from a separate PDF page count:
without it, completeness is INCONCLUSIVE rather than presumed.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from offline.document_processor import ProcessedDocument


@dataclass(frozen=True)
class StructureAudit:
    status: str
    reasons: tuple[str, ...]
    observed_pdf_pages: tuple[int, ...]
    missing_pdf_pages: tuple[int, ...]
    block_count: int
    image_count: int

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "observed_pdf_pages": list(self.observed_pdf_pages),
            "missing_pdf_pages": list(self.missing_pdf_pages),
            "block_count": self.block_count,
            "image_count": self.image_count,
            "scope": "STRUCTURE_ONLY_NOT_OCR_QUALITY",
        }


def audit_document_structure(document: ProcessedDocument, *, expected_pdf_pages: int | None = None) -> StructureAudit:
    """Fail on observable structural corruption; never invent missing metadata."""
    if expected_pdf_pages is not None and (type(expected_pdf_pages) is not int or expected_pdf_pages <= 0):
        raise ValueError("expected_pdf_pages must be a positive integer")
    if expected_pdf_pages is not None and document.document_type != "pdf":
        raise ValueError("PDF page count is applicable only to pdf")
    reasons: set[str] = set()
    seen_blocks: set[str] = set()
    seen_images: set[str] = set()
    observed_pages: set[int] = set()
    sheet_windows: dict[str, list[tuple[int, int]]] = defaultdict(list)

    for block in document.blocks:
        if block.block_identity:
            if block.block_identity in seen_blocks:
                reasons.add("duplicate_block_identity")
            seen_blocks.add(block.block_identity)
        if not isinstance(block.text, str) or not block.text.strip():
            reasons.add("empty_block")
        if document.document_type == "pdf":
            page = block.metadata.get("page_number")
            if type(page) is not int or page <= 0:
                reasons.add("invalid_pdf_page")
            else:
                observed_pages.add(page)
        if document.document_type == "xlsx":
            start, end, sheet = (
                block.metadata.get("row_start"),
                block.metadata.get("row_end"),
                block.metadata.get("sheet_name"),
            )
            if (
                type(start) is not int
                or type(end) is not int
                or start <= 0
                or end < start
                or not isinstance(sheet, str)
                or not sheet
            ):
                reasons.add("invalid_sheet_window")
            else:
                sheet_windows[sheet].append((start, end))

    for image in document.images:
        if image.image_id in seen_images:
            reasons.add("duplicate_image_identity")
        seen_images.add(image.image_id)
        if not image.data:
            reasons.add("empty_image_data")
        if document.document_type == "pdf":
            if type(image.page_number) is not int or image.page_number <= 0:
                reasons.add("invalid_pdf_page")
            else:
                observed_pages.add(image.page_number)

    for windows in sheet_windows.values():
        ordered = sorted(windows)
        for (_, previous_end), (current_start, _) in zip(ordered[:-1], ordered[1:], strict=True):
            if current_start <= previous_end:
                reasons.add("overlapping_sheet_windows")

    if document.requires_ocr and not document.images:
        reasons.add("ocr_required_but_no_page_images")
    missing: tuple[int, ...] = ()
    if document.document_type == "pdf" and expected_pdf_pages is not None:
        missing = tuple(sorted(set(range(1, expected_pdf_pages + 1)) - observed_pages))
        if missing:
            reasons.add("missing_pdf_page")
        if any(page > expected_pdf_pages for page in observed_pages):
            reasons.add("out_of_range_pdf_page")

    # Without an independent page count, even seemingly healthy PDF pages do
    # not prove nothing was lost at the beginning, middle or end.
    if reasons:
        status = "FAIL"
    elif document.document_type == "pdf" and expected_pdf_pages is None:
        status = "INCONCLUSIVE"
    else:
        status = "PASS_STRUCTURE"
    return StructureAudit(
        status=status,
        reasons=tuple(sorted(reasons)),
        observed_pdf_pages=tuple(sorted(observed_pages)),
        missing_pdf_pages=missing,
        block_count=len(document.blocks),
        image_count=len(document.images),
    )
