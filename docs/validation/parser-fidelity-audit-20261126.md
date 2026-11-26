# Parser Fidelity Audit — 2026-11-26

**Evidence class:** `REPO_VERIFIED` for deterministic fixture contracts only.

**Real-document fidelity:** `PENDING`.

**MinerU/Docling comparison:** `BLOCKED` — no authorized, representative comparison set was available.

**Base snapshot:** `03dc58e93b6ff9ca2c79d9adaa5d57149d55462d` (`main` at worktree creation).

**Audit time:** 2026-11-26 12:00 +08:00.

**Implementation branch:** `feature/rag01-document-parsing-20261126` (worktree; changes not yet committed).

## Scope and environment

This audit exercises existing parser/provider interfaces with generated local files and deterministic OCR/image providers. It does not establish extraction accuracy for real regulatory or business documents, legal currency, or parser superiority. No real source documents, PaddleOCR weights, BGE/CLIP weights, MinerU runtime, or Docling runtime were used.

| Component | Version |
|---|---:|
| Python | 3.10.12 |
| pytest | 8.4.2 |
| PyMuPDF | 1.28.2 |
| python-docx | 1.1.2 |
| openpyxl | 3.1.5 |
| Pillow | 12.2.0 |

The exact machine-readable capability and limitation inventory is [`parser-capability-matrix-20261126.json`](parser-capability-matrix-20261126.json). The test run uses synthetic/generated fixtures only.

This change adds 9 test functions covering 11 concrete cases (the malformed-container test runs once per PDF, DOCX and XLSX). The PDF fixtures include two pages each; the OCR hand-off fixture contains two scanned pages. These are generated local test artifacts, not corpus samples.

The local interpreter is Python 3.10.12 with Pillow 12.2.0, while the repository requires Pillow >=13 and the base-commit Actions test job ran on Python 3.11. Therefore local results are environment-specific and are not a substitute for CI on this branch's exact head. Python 3.11.15 is present in the container, but its environment does not have pytest installed.

## Parser observations

| Format/path | Verified by fixtures | Known boundary |
|---|---|---|
| TXT | UTF-8 BOM handling, empty file behavior, stable chunk identity and byte content digest | No page or layout locator |
| PDF | Page text metadata; opt-in per-page table blocks; scanned-page rendering; page/table locator survival through chunking; damaged container rejection | Tables remain page-local; no cross-page table continuation reconstruction; no real complex-layout or real OCR evaluation |
| DOCX | Heading, paragraph and table sequence; table index; explicit horizontal/vertical merged-cell output; stable source ID and digest | The library row-cell view repeats merged anchor text; current flattened output is not a semantic grid reconstruction and has no page/visual locator |
| XLSX | Sheet and inclusive row-window locators; merged non-anchor cells remain blank; numeric and literal unit strings; formula without cached value | `data_only=True` does not calculate formulas; uncached formulas are represented as empty values; formatting/layout is not preserved |
| Image/OCR hand-off | Synthetic scanned PDF page/image ordering, one-based page numbers, image indices and source provenance survive into OCR text chunks | Real PaddleOCR reading order/numeric accuracy and real CLIP embeddings are not tested |

Malformed PDF/DOCX/XLSX containers raise parse errors rather than returning empty-success documents. The binary byte limit is checked before parser import. A failed malformed-PDF snapshot does not commit source state and does not seal the target epoch.

## Verification record

Baseline at the feature worktree's base SHA, before these changes:

```text
pytest -q tests/offline/test_document_processor.py tests/offline/test_pdf_table_extraction.py tests/offline/test_image_processing.py tests/offline/test_snapshot_builder.py tests/offline/test_reconciliation_fixes.py
49 passed, 1 warning in 5.24s
```

After changes:

```text
pytest -q tests/offline/test_document_processor.py tests/offline/test_pdf_table_extraction.py tests/offline/test_image_processing.py tests/offline/test_snapshot_builder.py tests/offline/test_reconciliation_fixes.py
58 passed, 1 warning in 4.07s
```

Governance and architecture regression:

```text
pytest -q tests/test_architecture_contract.py tests/test_check_public_repository_boundary.py
143 passed, 1 warning in 17.95s
python3 scripts/check_public_repository_boundary.py --list-datasets
PASS; parser-capability-matrix-20261126.json is included in the tracked data inventory
python3 scripts/check_public_repository_boundary.py
PASS
python3 scripts/check_repo_consistency.py
PASS
git diff --check
PASS
```

The one warning in both runs is the existing Pydantic `GenerationResult.model_used` protected-namespace warning; no test failed or was skipped. The new OCR-page test first reproduced missing `image_index` metadata in OCR text chunks, then passed after propagating that locator from `ProcessedImage` in `SnapshotBuilder._ocr_text_chunks`.

## Evidence limits and next gate

- Fixture success establishes repository behavior under the versions above; it is not a measured real-document accuracy result.
- There is no legal/public representative sample in this run for complex layout, multi-page table continuation, scanned regulatory content, or unit/value extraction accuracy.
- No external parser was installed or run. Adding MinerU/Docling requires a separate authorized sample set, frozen comparison criteria, reproducible output and a demonstrated gap in the current parser.
- The runtime here is Python 3.10.12; GitHub Actions for the base commit ran the project test suite on Python 3.11. Local results and CI results remain separate.
- This package does not alter the persisted Qdrant/Elasticsearch schema, activate an epoch, change retrieval/ranking, or claim browser upload-to-answer validation.
