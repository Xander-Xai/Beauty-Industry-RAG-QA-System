"""
Parallel Multi-Path Recall Manager

Delegates to shared retrieval.parallel_recall implementation.
Service-specific sys.path setup is handled here.
"""

from __future__ import annotations

import os
import sys

# Ensure project root on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from retrieval.parallel_recall import ParallelRecallManager  # noqa: F401
