"""
ComplexityEvaluator -- thin wrapper importing from models.complexity_evaluator.

This file exists so that the generation-service microservice uses the same
implementation as the monolith. The canonical implementation lives in
models/complexity_evaluator.py.
"""

from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from models.complexity_evaluator import ComplexityEvaluator  # noqa: F401

__all__ = ["ComplexityEvaluator"]