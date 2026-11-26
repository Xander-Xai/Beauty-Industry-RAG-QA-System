"""Multi-format DocumentProcessor tests using tiny local fixtures."""

from __future__ import annotations

import pytest

from offline.document_processor import DocumentProcessor
from offline.text_ingestion import DocumentProcessor as Phase1DocumentProcessor


def _txt(tmp_path, text="hello world policy content"):
    path = tmp_path / "doc.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _pdf_text(tmp_path):
    import pymupdf

    path = tmp_path / "text.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Collagen moisturizer regulatory guidance for cosmetics industry.")
    document.save(path)
    document.close()
    return path


def _pdf_scanned(tmp_path):
    import pymupdf
    from PIL import Image

    image = Image.new("RGB", (200, 80), color="white")
    image_path = tmp_path / "scan.png"
    image.save(image_path)
    path = tmp_path / "scanned.pdf"
    document = pymupdf.open()
    page = document.new_page(width=220, height=100)
    page.insert_image(pymupdf.Rect(10, 10, 210, 90), filename=str(image_path))
    document.save(path)
    document.close()
    return path


def _docx(tmp_path):
    from docx import Document

    path = tmp_path / "doc.docx"
    document = Document()
    document.add_heading("Safety Guidance", level=1)
    document.add_paragraph("First paragraph about ingredients.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Ingredient"
    table.rows[0].cells[1].text = "Limit"
    document.save(path)
    return path


def _xlsx(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "doc.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ingredients"
    sheet.append(["Name", "Limit"])
    sheet.append(["Retinol", 0.3])
    sheet.append(["", None])
    sheet.append(["Niacinamide", 5])
    workbook.save(path)
    return path


def _docx_merged_cells(tmp_path):
    from docx import Document

    path = tmp_path / "merged.docx"
    document = Document()
    document.add_heading("Merged-cell structure", level=1)
    document.add_paragraph("before-table sentinel")
    table = document.add_table(rows=3, cols=3)
    table.cell(0, 0).text = "Horizontal anchor"
    table.cell(0, 2).text = "right"
    table.cell(1, 0).text = "Vertical anchor"
    table.cell(1, 1).text = "row two"
    table.cell(2, 1).text = "row three"
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(1, 0).merge(table.cell(2, 0))
    document.add_paragraph("after-table sentinel")
    document.save(path)
    return path


def _xlsx_structures(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "structures.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ingredient Values"
    sheet.merge_cells("A1:C1")
    sheet["A1"] = "Ingredient schedule"
    sheet.append(["Ingredient", "Amount", "Unit", "Formula"])
    sheet.append(["Retinol", 0.3, "0.3%", "=B3*100"])
    sheet.append(["Niacinamide", 5.0, "5 %", "=B4*100"])
    other = workbook.create_sheet("Notes")
    other.append(["Do not infer merged values"])
    workbook.save(path)
    return path


def test_txt_processor_matches_phase_one_chunk_identity(tmp_path):
    path = _txt(tmp_path, "Alpha beta gamma delta " * 30)
    new = DocumentProcessor(chunk_size=50, chunk_overlap=5)
    old = Phase1DocumentProcessor(chunk_size=50, chunk_overlap=5)
    new_chunks = new.process_chunks(path, role_mask=0, dept_mask=0, doc_version_epoch="phase_1")
    old_chunks = old.process(path, role_mask=0, dept_mask=0, doc_version_epoch="phase_1")
    assert [chunk.chunk_id for chunk in new_chunks] == [chunk.chunk_id for chunk in old_chunks]
    assert [chunk.text for chunk in new_chunks] == [chunk.text for chunk in old_chunks]


def test_txt_bom_and_empty_document(tmp_path):
    path = tmp_path / "bom.txt"
    path.write_text("\ufeffsafety first", encoding="utf-8")
    processed = DocumentProcessor().process(path)
    assert processed.document_type == "txt"
    assert processed.blocks[0].text == "safety first"
    assert not processed.requires_ocr

    empty = tmp_path / "empty.txt"
    empty.write_text("  \n", encoding="utf-8")
    assert DocumentProcessor().process(empty).blocks == []


def test_pdf_text_extraction_records_page_metadata(tmp_path):
    processed = DocumentProcessor().process(_pdf_text(tmp_path))
    assert processed.document_type == "pdf"
    assert not processed.requires_ocr
    assert processed.blocks
    assert processed.blocks[0].metadata["page_number"] == 1
    assert "Collagen" in processed.blocks[0].text


def test_scanned_pdf_requests_ocr_instead_of_empty_success(tmp_path):
    processed = DocumentProcessor().process(_pdf_scanned(tmp_path))
    assert processed.requires_ocr is True
    assert processed.images
    assert processed.images[0].mime_type == "image/png"
    assert processed.images[0].page_number == 1


def test_docx_preserves_structure(tmp_path):
    processed = DocumentProcessor().process(_docx(tmp_path))
    block_types = [block.block_type for block in processed.blocks]
    assert block_types == ["heading", "paragraph", "table"]
    assert processed.blocks[0].metadata["heading_level"] == 1
    assert "Ingredient" in processed.blocks[-1].text


def test_docx_merged_cells_are_explicit_and_ordered_through_chunking(tmp_path):
    import hashlib

    path = _docx_merged_cells(tmp_path)
    processor = DocumentProcessor(chunk_size=120, chunk_overlap=0)
    processed = processor.process(path, source_id="fixture/merged.docx")

    assert [block.block_type for block in processed.blocks] == ["heading", "paragraph", "table", "paragraph"]
    table = processed.blocks[2]
    assert table.metadata["table_index"] == 2
    assert table.text == (
        "Horizontal anchor | Horizontal anchor | right\nVertical anchor | row two | \nVertical anchor | row three |"
    )
    assert processed.source_id == "fixture/merged.docx"
    assert processed.content_hash == hashlib.sha256(path.read_bytes()).hexdigest()

    repeated = processor.process(path, source_id="fixture/merged.docx")
    assert repeated.doc_id == processed.doc_id
    assert repeated.content_hash == processed.content_hash
    chunks = processor.build_chunks(
        processed,
        role_mask=0,
        dept_mask=0,
        doc_version_epoch="parser-audit-20261126",
    )
    assert any(chunk.metadata.get("table_index") == table.metadata["table_index"] for chunk in chunks)
    assert all(chunk.doc_id == processed.doc_id for chunk in chunks)


def test_xlsx_merged_cells_numbers_units_formula_cache_and_locators(tmp_path):
    path = _xlsx_structures(tmp_path)
    processed = DocumentProcessor(xlsx_rows_per_block=2).process(path, source_id="fixture/values.xlsx")

    ingredient_blocks = [block for block in processed.blocks if block.metadata["sheet_name"] == "Ingredient Values"]
    notes_block = next(block for block in processed.blocks if block.metadata["sheet_name"] == "Notes")
    assert [(block.metadata["row_start"], block.metadata["row_end"]) for block in ingredient_blocks] == [(1, 2), (3, 4)]
    assert ingredient_blocks[0].text == "Ingredient schedule |  |  | \nIngredient | Amount | Unit | Formula"
    assert "0.3 | 0.3%" in ingredient_blocks[1].text
    assert "5 | 5 %" in ingredient_blocks[1].text
    # openpyxl's read-only data_only view has no calculated value when the file
    # contains a formula but no cached result; parsing does not evaluate it.
    assert ingredient_blocks[1].text.endswith("Niacinamide | 5 | 5 % |")
    assert notes_block.metadata["row_start"] == 1
    assert "Do not infer merged values" in notes_block.text


def test_xlsx_uses_sheet_row_windows(tmp_path):
    processor = DocumentProcessor(xlsx_rows_per_block=2)
    processed = processor.process(_xlsx(tmp_path))
    assert processed.document_type == "xlsx"
    assert processed.blocks
    first = processed.blocks[0]
    assert first.metadata["sheet_name"] == "Ingredients"
    assert first.metadata["row_start"] == 1
    assert first.metadata["row_end"] == 2
    assert "Retinol" in first.text


def test_unsupported_extension_and_limits(tmp_path):
    path = tmp_path / "doc.csv"
    path.write_text("a,b", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported document type"):
        DocumentProcessor().process(path)

    oversized = tmp_path / "big.txt"
    oversized.write_text("x" * 20, encoding="utf-8")
    with pytest.raises(ValueError, match="ingestion limit"):
        DocumentProcessor(max_document_bytes=8).process(oversized)


@pytest.mark.parametrize("extension", ["pdf", "docx", "xlsx"])
def test_corrupt_binary_containers_fail_explicitly(tmp_path, extension):
    path = tmp_path / f"corrupt.{extension}"
    path.write_bytes(b"not a valid document container")

    with pytest.raises(ValueError, match="cannot open"):
        DocumentProcessor().process(path)


def test_binary_size_limit_is_checked_before_parser_import(tmp_path, monkeypatch):
    import offline.document_processor as document_processor_module

    path = tmp_path / "too-large.pdf"
    path.write_bytes(b"%PDF-" + b"x" * 32)

    def unexpected_import():
        raise AssertionError("parser must not be imported before the size check")

    monkeypatch.setattr(document_processor_module, "_import_pymupdf", unexpected_import)
    with pytest.raises(ValueError, match="ingestion limit"):
        DocumentProcessor(max_binary_document_bytes=8).process(path)


@pytest.mark.parametrize("role,dept", [(-1, 0), (0, 2**32), (True, 0), (0, "1")])
def test_invalid_permission_masks_fail_closed(tmp_path, role, dept):
    path = _txt(tmp_path)
    with pytest.raises(ValueError, match="uint32"):
        DocumentProcessor().process_chunks(path, role_mask=role, dept_mask=dept, doc_version_epoch="epoch_1")


def test_chunk_limit_enforced_before_write(tmp_path):
    path = _txt(tmp_path, "abcdefghij" * 10)
    processor = DocumentProcessor(chunk_size=5, chunk_overlap=0, max_chunks=2)
    with pytest.raises(ValueError, match="2-chunk ingestion limit"):
        processor.process_chunks(path, role_mask=0, dept_mask=0, doc_version_epoch="epoch_1")


def test_source_identity_is_stable_across_roots(tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    (root_a / "guides").mkdir(parents=True)
    (root_b / "guides").mkdir(parents=True)
    (root_a / "guides" / "p.txt").write_text("same", encoding="utf-8")
    (root_b / "guides" / "p.txt").write_text("same", encoding="utf-8")
    _, first = DocumentProcessor(source_root=root_a).document_identity(root_a / "guides" / "p.txt")
    _, second = DocumentProcessor(source_root=root_b).document_identity(root_b / "guides" / "p.txt")
    assert first == second
