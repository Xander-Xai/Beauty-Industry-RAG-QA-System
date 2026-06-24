"""
FastAPI Dependencies (backward-compatible shim)

Parses user identity from request headers. In production mode (dev_mode=False),
X-User-* headers are NOT trusted — only JWT Bearer tokens are used for
authentication.

Note: current routes use common.auth.require_identity directly; this module
retains get_identity as a backward-compatibility alias.
"""

from __future__ import annotations

from common.auth import parse_identity

# Backward compatibility: old code may `from api.dependencies import get_identity`
get_identity = parse_identity
