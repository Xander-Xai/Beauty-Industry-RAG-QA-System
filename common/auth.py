"""
FastAPI authentication middleware and RBAC helpers.

Migrated from auth/user_identity.py and auth/bitmask_rbac.py, adapted for
async FastAPI request objects instead of Flask.

Public API:
    parse_identity(request) -> UserIdentity
    encode_role_mask(roles) -> int
    encode_dept_mask(depts) -> int
    is_allowed(doc_role, user_role, doc_dept, user_dept) -> bool
    build_milvus_filter(user_role_mask, user_dept_mask, epoch) -> str
    require_identity() -> Depends(...)   (FastAPI dependency)
"""

from __future__ import annotations

import logging
import re
import time
from typing import List, Optional

from fastapi import Request, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from common.config import get_config
from common.models import UserIdentity

_EPOCH_SAFE_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

logger = logging.getLogger(__name__)

_security = HTTPBearer(auto_error=False)


# ── JWT helpers ──────────────────────────────────────────────────────────


def _get_jwt_settings() -> dict:
    cfg = get_config().auth
    return {
        "secret": cfg.jwt_secret,
        "algorithm": "HS256",
        "expiry_hours": cfg.jwt_expiry_hours,
    }


def _decode_jwt(token: str) -> Optional[dict]:
    """Decode and validate a JWT, returning the payload dict or None."""
    try:
        import jwt as _jwt
    except ImportError:
        logger.warning("PyJWT not installed -- cannot decode JWT tokens")
        return None

    settings = _get_jwt_settings()
    try:
        payload = _jwt.decode(
            token,
            settings["secret"],
            algorithms=[settings["algorithm"]],
        )
        # Check expiry explicitly (PyJWT >=2.8 does this, but be safe)
        exp = payload.get("exp")
        if exp is not None and float(exp) < time.time():
            logger.warning("JWT token has expired")
            return None
        return payload
    except Exception as exc:
        logger.warning("JWT decode failed: %s", exc)
        return None


def _identity_from_jwt(payload: dict) -> UserIdentity:
    """Build a UserIdentity from a decoded JWT payload."""
    cfg = get_config()
    rbac = cfg.rbac
    user_id = payload.get("user_id") or payload.get("sub", "anonymous")

    role_mask = payload.get("role_mask")
    dept_mask = payload.get("dept_mask")

    # Fallback: encode from named roles / depts
    if role_mask is None and "roles" in payload:
        role_mask = _encode_from_names(payload["roles"], rbac.roles)
    if dept_mask is None and "depts" in payload:
        dept_mask = _encode_from_names(payload["depts"], rbac.departments)

    return UserIdentity(
        user_id=user_id,
        user_role_mask=role_mask if role_mask is not None else 0,
        user_dept_mask=dept_mask if dept_mask is not None else 0,
    )


# ── Public helpers ───────────────────────────────────────────────────────


def _encode_from_names(names: List[str], mapping: dict) -> int:
    """Encode a list of role/department names into a bitmask."""
    mask = 0
    for name in names:
        if name in mapping:
            mask |= mapping[name]
    return mask


def encode_role_mask(roles: List[str]) -> int:
    """Encode role names to a bitmask using the config-defined mapping."""
    return _encode_from_names(roles, get_config().rbac.roles)


def encode_dept_mask(depts: List[str]) -> int:
    """Encode department names to a bitmask using the config-defined mapping."""
    return _encode_from_names(depts, get_config().rbac.departments)


def is_allowed(
    doc_role_mask: int,
    user_role_mask: int,
    doc_dept_mask: int,
    user_dept_mask: int,
) -> bool:
    """
    RBAC check: can *user* access a document restricted by *doc* masks?

    Rules (mirrors auth/bitmask_rbac.py):
    - doc_role_mask == 0  =>  no role restriction (public)
    - user_role_mask == super_admin  =>  always allowed
    - Otherwise: role bits must overlap AND dept bits must overlap
      (doc_dept_mask == 0 means no dept restriction).
    """
    cfg = get_config().rbac
    if doc_role_mask == 0:
        if doc_dept_mask == 0:
            return True
        return (doc_dept_mask & user_dept_mask) != 0
    if user_role_mask == cfg.super_admin_mask:
        return True
    role_ok = (doc_role_mask & user_role_mask) != 0
    dept_ok = doc_dept_mask == 0 or (doc_dept_mask & user_dept_mask) != 0
    return role_ok and dept_ok


def build_milvus_filter(
    user_role_mask: int,
    user_dept_mask: int,
    knowledge_version_epoch: str,
) -> str:
    """
    Build a Milvus boolean expression that enforces RBAC + version gating.

    Example output::

        (role_mask == 0 OR ((role_mask & 5) != 0))
        AND (dept_mask == 0 OR ((dept_mask & 3) != 0))
        AND doc_version_epoch == '20260603_00'
        AND status == 'active'
    """
    # Input validation (§11 安全: 防止过滤注入)
    if not isinstance(user_role_mask, int) or not (0 <= user_role_mask <= 0xFFFFFFFF):
        raise ValueError(f"user_role_mask must be uint32, got {user_role_mask!r}")
    if not isinstance(user_dept_mask, int) or not (0 <= user_dept_mask <= 0xFFFFFFFF):
        raise ValueError(f"user_dept_mask must be uint32, got {user_dept_mask!r}")
    if not knowledge_version_epoch or not _EPOCH_SAFE_RE.match(knowledge_version_epoch):
        raise ValueError(
            f"knowledge_version_epoch must match [a-zA-Z0-9_-], "
            f"got {knowledge_version_epoch!r}"
        )

    rm0 = "(role_mask == 0)"
    rmu = f"((role_mask & {user_role_mask}) != 0)"
    dm0 = "(dept_mask == 0)"
    dmu = f"((dept_mask & {user_dept_mask}) != 0)"
    ep = f"doc_version_epoch == '{knowledge_version_epoch}'"
    st = "status == 'active'"
    return f"({rm0} OR {rmu}) AND ({dm0} OR {dmu}) AND {ep} AND {st}"


# ── FastAPI dependency ──────────────────────────────────────────────────


async def parse_identity(request: Request) -> UserIdentity:
    """
    Parse user identity from an incoming FastAPI request.

    Priority:
    1. ``Authorization: Bearer <jwt>``
    2. Dev-mode ``X-User-*`` headers (仅 dev_mode=True 时生效)
    3. Anonymous fallback
    """
    cfg = get_config()

    # 1. JWT Bearer token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        payload = _decode_jwt(token)
        if payload is not None:
            return _identity_from_jwt(payload)
        logger.warning("JWT decode failed, falling back to dev headers")

    # 2. Dev-mode headers — 仅在 dev_mode=True 时信任 Header
    if cfg.auth.dev_mode:
        role_str = request.headers.get("X-User-Role-Mask", "0")
        dept_str = request.headers.get("X-User-Dept-Mask", "0")
        user_id = request.headers.get("X-User-Id", "anonymous")
        logger.debug("dev_mode: trusting X-User-* headers for user=%s", user_id)
        return UserIdentity(
            user_id=user_id,
            user_role_mask=int(role_str),
            user_dept_mask=int(dept_str),
        )

    # 3. 生产模式：无有效 JWT 则返回匿名（零掩码），不信任 Header
    return UserIdentity(user_id="anonymous", user_role_mask=0, user_dept_mask=0)


async def require_identity(request: Request) -> UserIdentity:
    """
    FastAPI dependency that enforces authentication.

    Unlike ``parse_identity``, this raises 401 when no valid credentials
    are found in non-dev mode.
    """
    identity = await parse_identity(request)
    cfg = get_config()
    if not cfg.auth.dev_mode and identity.user_id == "anonymous":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return identity


# ── Token generation (for tests) ────────────────────────────────────────


def generate_token(
    user_id: str,
    roles: Optional[List[str]] = None,
    depts: Optional[List[str]] = None,
    role_mask: Optional[int] = None,
    dept_mask: Optional[int] = None,
) -> str:
    """
    Generate a JWT token (for testing and auth-service integration).

    Returns the encoded token string.
    """
    try:
        import jwt as _jwt
    except ImportError:
        raise RuntimeError("PyJWT is not installed -- cannot generate tokens")

    cfg = get_config()
    now = int(time.time())
    payload: dict = {
        "sub": user_id,
        "user_id": user_id,
        "iat": now,
        "exp": now + cfg.auth.jwt_expiry_hours * 3600,
    }

    if role_mask is not None:
        payload["role_mask"] = role_mask
    elif roles:
        payload["role_mask"] = encode_role_mask(roles)
        payload["roles"] = roles

    if dept_mask is not None:
        payload["dept_mask"] = dept_mask
    elif depts:
        payload["dept_mask"] = encode_dept_mask(depts)
        payload["depts"] = depts

    return _jwt.encode(
        payload,
        cfg.auth.jwt_secret,
        algorithm="HS256",
    )
