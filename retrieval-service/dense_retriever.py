"""
Thin wrapper re-exporting DenseRetriever from the original retrieval package.

The actual implementation lives at retrieval/dense_retriever.py and is
imported via sys.path so we avoid code duplication while keeping the
service self-contained.
"""

import sys

sys.path.insert(0, "/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge")

from retrieval.dense_retriever import DenseRetriever  # noqa: F401

__all__ = ["DenseRetriever"]
