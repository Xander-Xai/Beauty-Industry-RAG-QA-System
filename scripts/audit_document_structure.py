"""Read-only structural audit CLI for a local source document.

Example:
python -m scripts.audit_document_structure /path/to/approved.pdf

Output omits source text and source path. It is a preflight diagnostic only,
not an OCR fidelity or retrieval-quality score.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmarks.document_structure_audit import audit_document_structure
from offline.document_processor import DocumentProcessor


def inspect_source(path: Path) -> dict:
    """Bind a structural report to a local parsed document and its content hash."""
    parsed = DocumentProcessor().process(path)
    page_count: int | None = None
    if parsed.document_type == "pdf":
        from offline.document_processor import _import_pymupdf

        pymupdf = _import_pymupdf()
        with pymupdf.open(path) as source:
            page_count = len(source)
    report = audit_document_structure(parsed, expected_pdf_pages=page_count)
    return {
        "schema_version": "document-structure-audit/v1",
        "document_type": parsed.document_type,
        "source_sha256": parsed.content_hash,
        "expected_pdf_pages": page_count,
        "requires_ocr": parsed.requires_ocr,
        **report.as_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit source structure; never report OCR accuracy")
    parser.add_argument("path", type=Path, help="Local authorized PDF/DOCX/XLSX/TXT source")
    args = parser.parse_args(argv)
    try:
        report = inspect_source(args.path)
    except Exception as exc:
        # No paths, document text, passwords, or stack traces in the public report.
        print(
            json.dumps(
                {
                    "schema_version": "document-structure-audit/v1",
                    "status": "FAIL",
                    "reasons": ["SOURCE_PROCESSING_ERROR"],
                    "error_type": type(exc).__name__,
                    "scope": "STRUCTURE_ONLY_NOT_OCR_QUALITY",
                },
                sort_keys=True,
            )
        )
        return 4
    print(json.dumps(report, sort_keys=True))
    return {"PASS_STRUCTURE": 0, "FAIL": 2, "INCONCLUSIVE": 3}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
