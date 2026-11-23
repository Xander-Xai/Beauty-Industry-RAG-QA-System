"""Offline document structure audit with synthetic blocks; no OCR success claims."""

from types import SimpleNamespace

import pytest

from benchmarks.document_structure_audit import audit_document_structure


def block(identity, *, text="source", page=None, sheet=None, start=None, end=None):
    meta = {}
    if page is not None:
        meta["page_number"] = page
    if sheet is not None:
        meta.update(sheet_name=sheet, row_start=start, row_end=end)
    return SimpleNamespace(block_identity=identity, text=text, metadata=meta)


def image(identity, page, data=b"png"):
    return SimpleNamespace(image_id=identity, page_number=page, data=data)


def doc(kind="pdf", blocks=None, images=None, requires_ocr=False):
    return SimpleNamespace(
        document_type=kind,
        blocks=blocks or [],
        images=images or [],
        requires_ocr=requires_ocr,
    )


def test_pdf_page_with_ocr_image_complete_structure():
    candidate = doc(blocks=[block("page:0", page=1)], images=[image("scan:1", 2)], requires_ocr=True)
    report = audit_document_structure(candidate, expected_pdf_pages=2)
    assert report.status == "PASS_STRUCTURE"
    assert report.observed_pdf_pages == (1, 2)
    assert report.as_dict()["scope"] == "STRUCTURE_ONLY_NOT_OCR_QUALITY"


def test_missing_pdf_page_fails_even_if_other_pages_present():
    candidate = doc(blocks=[block("page:0", page=1), block("page:2", page=3)])
    report = audit_document_structure(candidate, expected_pdf_pages=3)
    assert report.status == "FAIL"
    assert report.missing_pdf_pages == (2,)
    assert "missing_pdf_page" in report.reasons


def test_pdf_without_independent_page_count_is_inconclusive():
    assert audit_document_structure(doc(blocks=[block("page:0", page=1)])).status == "INCONCLUSIVE"


def test_ocr_requested_but_rasterization_lost_image_fails():
    report = audit_document_structure(doc(requires_ocr=True), expected_pdf_pages=1)
    assert report.status == "FAIL"
    assert "ocr_required_but_no_page_images" in report.reasons


def test_duplicate_block_identity_and_invalid_page_fail():
    report = audit_document_structure(
        doc(blocks=[block("duplicate", page=1), block("duplicate", page=True)]),
        expected_pdf_pages=1,
    )
    assert report.status == "FAIL"
    assert {"duplicate_block_identity", "invalid_pdf_page"} <= set(report.reasons)


def test_image_collision_or_empty_payload_fails():
    report = audit_document_structure(
        doc(images=[image("same", 1), image("same", 2, b"")]), expected_pdf_pages=2
    )
    assert {"duplicate_image_identity", "empty_image_data"} <= set(report.reasons)


def test_spreadsheet_overlapping_window_for_same_sheet_fails():
    report = audit_document_structure(
        doc(
            "xlsx",
            blocks=[
                block("a", sheet="S1", start=1, end=50),
                block("b", sheet="S1", start=50, end=75),
            ],
        )
    )
    assert report.status == "FAIL"
    assert "overlapping_sheet_windows" in report.reasons


def test_adjacent_spreadsheet_windows_and_separate_sheets_allowed():
    report = audit_document_structure(
        doc(
            "xlsx",
            blocks=[
                block("a", sheet="S1", start=1, end=50),
                block("b", sheet="S1", start=51, end=60),
                block("c", sheet="S2", start=1, end=50),
            ],
        )
    )
    assert report.status == "PASS_STRUCTURE"


def test_docx_empty_block_fails():
    report = audit_document_structure(doc("docx", blocks=[block("table:1", text="  ")]))
    assert report.status == "FAIL" and "empty_block" in report.reasons


@pytest.mark.parametrize("count", [0, -1, True, 1.5])
def test_invalid_expected_page_count_rejected(count):
    with pytest.raises(ValueError):
        audit_document_structure(doc(), expected_pdf_pages=count)


def test_pdf_page_count_cannot_be_attached_to_docx():
    with pytest.raises(ValueError):
        audit_document_structure(doc("docx"), expected_pdf_pages=1)
