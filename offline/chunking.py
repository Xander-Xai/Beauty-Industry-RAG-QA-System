"""Deterministic, bounded chunking primitives shared by offline processors.

Chunk identity is derived from the logical document identity, the document
content hash, a structural block identity, the chunk ordinal, and the chunk
content digest. For plain TXT documents the block identity is empty, so the
resulting logical ``chunk_id`` matches the Phase 1 text-ingestion slice and
existing snapshots remain interoperable.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field

POINT_NAMESPACE = uuid.UUID("74f7d957-77e6-4cac-9af6-a5f09c081215")


@dataclass(frozen=True)
class ChunkUnit:
    """A bounded, deterministic chunk plus the metadata needed for retrieval."""

    text: str
    chunk_index: int
    block_identity: str
    metadata: dict = field(default_factory=dict)


def chunk_identity(
    doc_id: str,
    content_hash: str,
    block_identity: str,
    chunk_index: int,
    text: str,
) -> str:
    """Return the stable logical ``chunk_id`` for one chunk."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if block_identity:
        identity = f"{doc_id}:{content_hash}:{block_identity}:{chunk_index}:{digest}"
    else:
        identity = f"{doc_id}:{content_hash}:{chunk_index}:{digest}"
    return str(uuid.uuid5(POINT_NAMESPACE, identity))


def split_character_windows(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Split ``text`` into overlapping character windows.

    This reproduces the Phase 1 TXT chunk boundaries exactly: a step of
    ``chunk_size - chunk_overlap`` and a stop once the window reaches the end
    of the text.
    """
    if not text:
        return []
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    if type(chunk_overlap) is not int or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_overlap must be an integer in [0, chunk_size)")
    step = chunk_size - chunk_overlap
    windows: list[str] = []
    for offset in range(0, len(text), step):
        window = text[offset : offset + chunk_size]
        if not window:
            continue
        windows.append(window)
        if offset + chunk_size >= len(text):
            break
    return windows


class CharacterChunker:
    """Character-based chunker with a fixed size and overlap."""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        if type(chunk_size) is not int or chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")
        if type(chunk_overlap) is not int or not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk_overlap must be an integer in [0, chunk_size)")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, text: str) -> list[str]:
        return split_character_windows(text, self.chunk_size, self.chunk_overlap)

    def build_units(
        self,
        *,
        doc_id: str,
        content_hash: str,
        block_identity: str,
        base_metadata: dict | None,
        texts: list[str],
        start_index: int = 0,
    ) -> list[ChunkUnit]:
        units = []
        for offset, text in enumerate(texts):
            chunk_index = start_index + offset
            units.append(
                ChunkUnit(
                    text=text,
                    chunk_index=chunk_index,
                    block_identity=block_identity,
                    metadata={**(base_metadata or {}), "chunk_index": chunk_index},
                )
            )
        return units


def chunk_blocks(
    blocks,
    *,
    doc_id: str,
    content_hash: str,
    chunk_size: int,
    chunk_overlap: int,
    start_index: int = 0,
) -> list[ChunkUnit]:
    """Pack document blocks into bounded chunks deterministically.

    Consecutive blocks are merged only while the merged text stays within
    ``chunk_size``. A single block larger than ``chunk_size`` is split with the
    same character-window algorithm used for plain text, so a TXT document with
    one block yields the Phase 1 chunk boundaries.
    """
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    if type(chunk_overlap) is not int or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_overlap must be an integer in [0, chunk_size)")

    units: list[ChunkUnit] = []
    next_index = start_index
    buffer_text: str | None = None
    buffer_meta: dict = {}
    buffer_blocks: list[str] = []

    def flush_buffer() -> None:
        nonlocal buffer_text, buffer_meta, buffer_blocks, next_index
        if buffer_text is None:
            return
        block_identity = "|".join(identity for identity in buffer_blocks if identity)
        units.append(
            ChunkUnit(
                text=buffer_text,
                chunk_index=next_index,
                block_identity=block_identity,
                metadata={**buffer_meta, "chunk_index": next_index},
            )
        )
        next_index += 1
        buffer_text = None
        buffer_meta = {}
        buffer_blocks = []

    def start_buffer(block) -> None:
        nonlocal buffer_text, buffer_meta, buffer_blocks
        buffer_text = block.text
        buffer_meta = dict(block.metadata)
        buffer_blocks = [block.block_identity] if block.block_identity else []

    for block in blocks:
        text = block.text
        if not text:
            continue
        if len(text) > chunk_size:
            flush_buffer()
            for window in split_character_windows(text, chunk_size, chunk_overlap):
                units.append(
                    ChunkUnit(
                        text=window,
                        chunk_index=next_index,
                        block_identity=block.block_identity,
                        metadata={**block.metadata, "chunk_index": next_index},
                    )
                )
                next_index += 1
            continue

        if buffer_text is None:
            start_buffer(block)
        elif len(buffer_text) + 1 + len(text) <= chunk_size:
            buffer_text = f"{buffer_text}\n{text}"
            buffer_meta = {**buffer_meta, **{k: v for k, v in block.metadata.items() if k not in buffer_meta}}
            if block.block_identity:
                buffer_blocks.append(block.block_identity)
        else:
            flush_buffer()
            start_buffer(block)

    flush_buffer()
    return units
