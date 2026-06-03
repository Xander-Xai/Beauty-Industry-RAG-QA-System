"""
Thin wrapper re-exporting CLIPRetriever from the original retrieval package.

The actual implementation lives at retrieval/clip_retriever.py and is
imported via sys.path so we avoid code duplication while keeping the
service self-contained.
"""

import sys

sys.path.insert(0, "/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge")

from retrieval.clip_retriever import CLIPRetriever  # noqa: F401

__all__ = ["CLIPRetriever"]
