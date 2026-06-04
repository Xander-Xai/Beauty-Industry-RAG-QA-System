"""
Thin wrapper re-exporting DenseRetriever from the original retrieval package.

The actual implementation lives at retrieval/dense_retriever.py and is
imported via sys.path so we avoid code duplication while keeping the
service self-contained.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retrieval.dense_retriever import DenseRetriever  # noqa: F401

__all__ = ["DenseRetriever"]
