"""Opt-in PDF table preservation without silently changing existing parsing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from offline.document_processor import DocumentBlock, DocumentProcessor


class _Table:
    def __init__(self, rows):
        self.rows = rows

    def extract(self):
        return self.rows


class _Page:
    def __init__(self, tables):
        self.tables = tables

    def find_tables(self):
        return SimpleNamespace(tables=self.tables)


def test_pdf_table_rows_and_provenance_are_preserved():
    processor = DocumentProcessor(pdf_extract_tables=True)
    blocks = processor._extract_pdf_tables(
        _Page(
            [
                _Table(
                    [
                        ["INCI Name", "Limit"],
                        ["Niacinamide", "5%"],
                        ["Retinol", None],
                    ]
                )
            ]
        ),
        page_index=2,
    )
    assert len(blocks) == 1
    block = blocks[0]
    assert block.block_type == "table"
    assert block.block_identity == "page:2:table:0"
    assert block.metadata == {"format": "pdf", "page_number": 3, "table_index": 0}
    assert block.text == "INCI Name | Limit\nNiacinamide | 5%\nRetinol |"


def test_detection_unavailable_or_malformed_fails_closed():
    processor = DocumentProcessor(pdf_extract_tables=True)
    with pytest.raises(ValueError, match="unavailable"):
        processor._extract_pdf_tables(object(), 0)
    with pytest.raises(ValueError, match="Invalid PDF table row"):
        processor._extract_pdf_tables(_Page([_Table(["not-a-row"])]), 0)
    with pytest.raises(ValueError, match="no table collection"):
        processor._extract_pdf_tables(_Page(None), 0)


def test_pdf_default_does_not_run_table_detector(tmp_path, monkeypatch):
    import pymupdf

    pdf = tmp_path / "document.pdf"
    source = pymupdf.open()
    source.new_page().insert_text((70, 70), "Cosmetics product labelling and ingredient compliance policy.")
    source.save(pdf)
    source.close()

    def unexpected(*_args):
        raise AssertionError("table detector must stay off in the default path")

    monkeypatch.setattr(DocumentProcessor, "_extract_pdf_tables", unexpected)
    result = DocumentProcessor().process(pdf)
    assert [block.block_type for block in result.blocks] == ["page"]


def test_pdf_optin_passes_source_page_into_table_blocks(tmp_path, monkeypatch):
    import pymupdf

    pdf = tmp_path / "document.pdf"
    source = pymupdf.open()
    source.new_page().insert_text((70, 70), "Cosmetics regulatory text with an additional controlled table.")
    source.save(pdf)
    source.close()

    def table_finder(_self, _page, page_index):
        return [
            DocumentBlock(
                block_type="table",
                text="Ingredient | Limit",
                order=page_index,
                block_identity=f"page:{page_index}:table:0",
                metadata={"format": "pdf", "page_number": page_index + 1, "table_index": 0},
            )
        ]

    monkeypatch.setattr(DocumentProcessor, "_extract_pdf_tables", table_finder)
    result = DocumentProcessor(pdf_extract_tables=True).process(pdf)
    assert [block.block_type for block in result.blocks] == ["page", "table"]
    assert result.blocks[1].metadata["page_number"] == 1


def test_non_boolean_optin_flag_fails_fast():
    with pytest.raises(ValueError, match="boolean"):
        DocumentProcessor(pdf_extract_tables="yes")


def test_real_pymupdf_table_grid_preserves_rows_in_optin_pdf(tmp_path):
    """Local real PyMuPDF table detector: synthetic drawn table, not real corpus."""
    import pymupdf

    path = tmp_path / "synthetic-grid.pdf"
    document = pymupdf.open()
    page = document.new_page(width=500, height=250)
    for x in (40, 230, 420):
        page.draw_line((x, 40), (x, 180), color=(0, 0, 0), width=1)
    for y in (40, 110, 180):
        page.draw_line((40, y), (420, y), color=(0, 0, 0), width=1)
    for cell, x, y in (
        ("Name", 55, 80),
        ("Limit", 245, 80),
        ("Niacinamide", 55, 150),
        ("5 percent", 245, 150),
    ):
        page.insert_text((x, y), cell, fontsize=12)
    document.save(path)
    document.close()

    processed = DocumentProcessor(pdf_extract_tables=True).process(path)
    tables = [block for block in processed.blocks if block.block_type == "table"]
    assert len(tables) == 1
    assert tables[0].text == "Name | Limit\nNiacinamide | 5 percent"
    assert tables[0].metadata["page_number"] == 1
    assert tables[0].metadata["table_index"] == 0


def test_pymupdf_layout_gate_is_disabled_when_supported():
    class ModernPage:
        def __init__(self):
            self.called = None

        def find_tables(self, *, use_layout=True):
            self.called = use_layout
            if use_layout:
                return SimpleNamespace(tables=[])
            return SimpleNamespace(tables=[_Table([["INCI", "Limit"], ["A", "2%"]])])

    page = ModernPage()
    result = DocumentProcessor(pdf_extract_tables=True)._extract_pdf_tables(page, 0)
    assert page.called is False
    assert len(result) == 1
    assert result[0].text == "INCI | Limit\nA | 2%"


def test_legacy_pymupdf_table_detector_signature_remains_supported():
    page = _Page([_Table([["INCI", "Limit"], ["A", "2%"]])])
    result = DocumentProcessor(pdf_extract_tables=True)._extract_pdf_tables(page, 0)
    assert len(result) == 1
    assert result[0].metadata["page_number"] == 1
