"""Deterministic chunking contract tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from offline.chunking import (
    CharacterChunker,
    ChunkUnit,
    chunk_blocks,
    chunk_identity,
    split_character_windows,
)


@dataclass
class _Block:
    text: str
    order: int
    block_identity: str
    metadata: dict


def test_character_windows_match_phase_one_boundaries():
    text = "A" * 40 + "\n\n" + "B" * 40 + "\n\n" + "C" * 40
    windows = split_character_windows(text, 50, 5)
    step = 45
    expected = []
    for offset in range(0, len(text), step):
        window = text[offset : offset + 50]
        if not window:
            continue
        expected.append(window)
        if offset + 50 >= len(text):
            break
    assert windows == expected


def test_chunk_identity_matches_phase_one_txt_formula():
    import hashlib

    doc_id = "doc"
    content_hash = "hash"
    text = "hello"
    expected_payload = f"{doc_id}:{content_hash}:0:{hashlib.sha256(text.encode()).hexdigest()}"
    import uuid

    from offline.chunking import POINT_NAMESPACE

    assert chunk_identity(doc_id, content_hash, "", 0, text) == str(uuid.uuid5(POINT_NAMESPACE, expected_payload))


def test_chunk_identity_distinguishes_blocks():
    a = chunk_identity("doc", "hash", "page:0", 0, "same")
    b = chunk_identity("doc", "hash", "page:1", 0, "same")
    assert a != b


def test_chunk_blocks_packs_small_blocks_and_splits_large_ones():
    blocks = [
        _Block("short one", 0, "p:0", {"block_type": "paragraph"}),
        _Block("short two", 1, "p:1", {"block_type": "paragraph"}),
        _Block("X" * 120, 2, "p:2", {"block_type": "paragraph"}),
    ]
    units = chunk_blocks(blocks, doc_id="d", content_hash="c", chunk_size=50, chunk_overlap=0)
    assert all(len(unit.text) <= 50 for unit in units)
    assert [unit.chunk_index for unit in units] == list(range(len(units)))
    assert isinstance(units[0], ChunkUnit)


def test_chunker_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        CharacterChunker(chunk_size=0)
    with pytest.raises(ValueError):
        CharacterChunker(chunk_size=10, chunk_overlap=10)
    with pytest.raises(ValueError):
        CharacterChunker(chunk_size=10, chunk_overlap=True)
