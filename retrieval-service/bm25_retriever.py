"""
Thin wrapper re-exporting BM25Retriever from the original retrieval package.

The actual implementation lives at retrieval/bm25_retriever.py and is
imported via sys.path so we avoid code duplication while keeping the
service self-contained.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retrieval.bm25_retriever import BM25Retriever  # noqa: F401

__all__ = ["BM25Retriever"]
