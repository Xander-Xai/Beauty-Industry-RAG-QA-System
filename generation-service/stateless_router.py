"""
Stateless Router — delegates to shared router.stateless_router.

Service-specific sys.path setup is handled here.
"""

from __future__ import annotations

import os
import sys

# Ensure project root on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from router.stateless_router import StatelessRouter  # noqa: F401
