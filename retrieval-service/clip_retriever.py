"""
Thin wrapper re-exporting CLIPRetriever from the original retrieval package.

The actual implementation lives at retrieval/clip_retriever.py and is
imported via sys.path so we avoid code duplication while keeping the
service self-contained.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from retrieval.clip_retriever import CLIPRetriever  # noqa: F401

__all__ = ["CLIPRetriever"]
