"""
Thin wrapper re-exporting BM25Retriever from the original retrieval package.

The actual implementation lives at retrieval/bm25_retriever.py and is
imported via sys.path so we avoid code duplication while keeping the
service self-contained.
"""

import sys

sys.path.insert(0, "/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge")

from retrieval.bm25_retriever import BM25Retriever  # noqa: F401

__all__ = ["BM25Retriever"]
