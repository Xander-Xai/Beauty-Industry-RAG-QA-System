"""
FastAPI authentication middleware and RBAC helpers.

Migrated from auth/user_identity.py and auth/bitmask_rbac.py, adapted for
async FastAPI request objects instead of Flask.

Public API:
    parse_identity(request) -> UserIdentity
    encode_role_mask(roles) -> int
    encode_dept_mask(depts) -> int
    is_allowed(doc_role, user_role, doc_dept, user_dept) -> bool
    build_qdrant_filter(user_role_mask, user_dept_mask, epoch) -> Filter
    require_identity() -> Depends(...)   (FastAPI dependency)
"""

from __future__ import annotations

import logging
import os
import re
import time

from fastapi import HTTPException, Request, status
from fastapi.security import HTTPBearer

from auth.jwt_auth import verify_token as verify_rs256_token
from common.config import get_config
from common.models import UserIdentity

_EPOCH_SAFE_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
# SEC-3: doc_id 校验正则 — 防止查询表达式注入
_DOC_ID_SAFE_RE = re.compile(r"^[a-zA-Z0-9_\-.]{1,128}$")

logger = logging.getLogger(__name__)


def validate_doc_id(doc_id: str) -> str:
    """校验 doc_id 格式，防止查询注入。无效值抛出 ValueError。"""
    if not doc_id or not _DOC_ID_SAFE_RE.match(doc_id):
        raise ValueError(f"无效的 doc_id 格式: {doc_id!r}")
    return doc_id


def is_admin_role_mask(user_role_mask: int) -> bool:
    """Return True when the role mask matches the configured admin identity."""
    cfg = get_config().rbac
    admin_mask = cfg.roles.get("admin")
    return user_role_mask in {
        cfg.super_admin_mask,
        admin_mask,
    }

_security = HTTPBearer(auto_error=False)


# ── JWT helpers ──────────────────────────────────────────────────────────


def _get_jwt_settings() -> dict:
    """Legacy HS256 compatibility settings.

    The primary auth flow now uses `auth.jwt_auth` (RS256 keypair). We keep the
    HS256 path only for backward compatibility with older tests and tokens.
    """
    cfg = get_config().auth
    secret = os.environ.get("JWT_SECRET", "").strip() or cfg.jwt_secret
    if not secret:
        return {
            "secret": "",
            "algorithm": "HS256",
            "expiry_hours": 0,
            "enabled": False,
        }
    return {
        "secret": secret,
        "algorithm": "HS256",
        "expiry_hours": cfg.jwt_expiry_hours,
        "enabled": True,
    }


def _decode_jwt(token: str) -> dict | None:
    """Decode and validate a JWT, preferring the RS256 browser auth contract."""
    rs256_payload = verify_rs256_token(token, "access")
    if rs256_payload is not None:
        return rs256_payload

    settings = _get_jwt_settings()
    if not settings["enabled"]:
        return None

    try:
        import jwt as _jwt
    except ImportError:
        logger.warning("PyJWT not installed -- cannot decode legacy HS256 JWT tokens")
        return None

    try:
        payload = _jwt.decode(
            token,
            settings["secret"],
            algorithms=[settings["algorithm"]],
        )
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


def _encode_from_names(names: list[str], mapping: dict) -> int:
    """Encode a list of role/department names into a bitmask."""
    mask = 0
    for name in names:
        if name in mapping:
            mask |= mapping[name]
    return mask


def encode_role_mask(roles: list[str]) -> int:
    """Encode role names to a bitmask using the config-defined mapping."""
    return _encode_from_names(roles, get_config().rbac.roles)


def encode_dept_mask(depts: list[str]) -> int:
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
    - configured admin / super_admin  =>  always allowed
    - Otherwise: role bits must overlap AND dept bits must overlap
      (doc_dept_mask == 0 means no dept restriction).
    """
    if is_admin_role_mask(user_role_mask):
        return True
    if doc_role_mask == 0:
        if doc_dept_mask == 0:
            return True
        return (doc_dept_mask & user_dept_mask) != 0
    role_ok = (doc_role_mask & user_role_mask) != 0
    dept_ok = doc_dept_mask == 0 or (doc_dept_mask & user_dept_mask) != 0
    return role_ok and dept_ok


def build_qdrant_filter(
    user_role_mask: int,
    user_dept_mask: int,
    knowledge_version_epoch: str,
):
    """
    Build a Qdrant Filter object.

    Qdrant pre-filter 处理 status == 'active'，并在显式激活知识版本时
    处理 doc_version_epoch。Qdrant Filter 不支持位掩码，因此 RBAC 权限
    仍在 Python 层通过 is_allowed() 二次校验。

    安全：对所有输入进行类型和范围验证。
    """
    from auth.bitmask_rbac import build_qdrant_filter as _build
    return _build(user_role_mask, user_dept_mask, knowledge_version_epoch)


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

    # 1. JWT Bearer token（仅在 JWT 启用时尝试解码）
    jwt_settings = _get_jwt_settings()
    if jwt_settings.get("enabled", True):
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = _decode_jwt(token)
            if payload is not None:
                return _identity_from_jwt(payload)
            logger.warning("JWT decode failed, falling back to dev headers")

    # 2. Dev-mode headers — 仅在 dev_mode=True 时信任 Header
    if cfg.auth.dev_mode:
        user_id = request.headers.get("X-User-ID", "anonymous")
        role_str = request.headers.get("X-Role-Mask", "0")
        dept_str = request.headers.get("X-Dept-Mask", "0")
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
    roles: list[str] | None = None,
    depts: list[str] | None = None,
    role_mask: int | None = None,
    dept_mask: int | None = None,
) -> str:
    """
    Generate a JWT token (for testing and auth-service integration).

    Returns the encoded token string.
    """
    try:
        import jwt as _jwt
    except ImportError:
        raise RuntimeError("PyJWT is not installed -- cannot generate tokens") from None

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
