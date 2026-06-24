"""
LLMClient -- thin wrapper importing from models.llm_client.

This file exists so that the generation-service microservice uses the same
implementation as the monolith. The canonical implementation lives in
models/llm_client.py.
"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from models.llm_client import LLMClient  # noqa: F401

__all__ = ["LLMClient"]