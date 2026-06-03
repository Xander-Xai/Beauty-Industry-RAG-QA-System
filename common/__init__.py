"""
common/ - Shared infrastructure for the automotive knowledge RAG microservices.

Provides:
- Config loading (singleton)
- Inter-service Pydantic models
- Exception hierarchy
- Auth middleware and RBAC helpers
- Async HTTP client for service-to-service calls
- Lightweight metrics and alerting
"""

from __future__ import annotations

__version__ = "2.0.0"
