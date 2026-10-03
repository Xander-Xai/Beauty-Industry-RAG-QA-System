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

from auth.jwt_auth import get_jwt_config as get_rs256_jwt_config
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
    """Decode and validate a JWT, preferring the RS256 browser auth contract.

    RS256 verification is independent of the legacy HS256 secret, but it still
    honors its own enable switch: ``JWT_ALGORITHM`` must be configured. Unsetting
    the algorithm disables the RS256 path, matching ``/api/auth/metadata`` and the
    login/refresh endpoints, which report JWT auth as disabled.
    """
    if get_rs256_jwt_config().enabled:
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


#: Canonical permission-mask bound: 32-bit unsigned, matching auth.bitmask_rbac.
_MAX_UINT32 = 0xFFFFFFFF


def _validate_permission_mask_claim(value, claim_name: str) -> int:
    """Validate one permission-mask claim against the canonical uint32 contract.

    JWT authorization-claim validation at identity ingress. A valid signature only
    proves the token came from the key holder; it says nothing about whether the
    permission claims inside are well-formed. Previously the raw claim went straight
    into ``UserIdentity``, whose mask fields are plain ``int``, so Pydantic coerced
    ``"1"`` to ``1`` and a stringly-typed mask became an authenticated identity.

    Strict by construction:

    * ``type(value) is int`` — not ``isinstance``, because ``isinstance(True, int)``
      is True and a JSON ``true`` would otherwise become mask 1;
    * ``0 <= value <= 0xFFFFFFFF``;
    * no ``int(value)`` coercion, no narrowing, no fallback.

    Raises ``ValueError`` so the caller can fail closed rather than construct an
    identity from an unvalidated claim.
    """
    if type(value) is not int:
        raise ValueError(f"JWT claim {claim_name!r} must be an integer, got {type(value).__name__}")
    if not 0 <= value <= _MAX_UINT32:
        raise ValueError(f"JWT claim {claim_name!r} must be within [0, {_MAX_UINT32}], got {value}")
    return value


def _identity_from_jwt(payload: dict) -> UserIdentity:
    """Build a UserIdentity from a decoded JWT payload.

    Permission claims are strictly validated *before* ``UserIdentity`` is built, so
    no downstream Pydantic coercion can launder a malformed mask. A claim that is
    present but invalid is an error: it is never treated as absent, and it never
    falls back to the named-role encoding.
    """
    cfg = get_config()
    rbac = cfg.rbac
    user_id = payload.get("user_id") or payload.get("sub", "anonymous")

    # Present-but-malformed must not be confused with absent. `"role_mask" in payload`
    # is True for an explicit JSON null, which is a malformed claim, not a missing one.
    role_mask = None
    if "role_mask" in payload:
        role_mask = _validate_permission_mask_claim(payload["role_mask"], "role_mask")
    elif "roles" in payload:
        role_mask = _encode_from_names(payload["roles"], rbac.roles)

    dept_mask = None
    if "dept_mask" in payload:
        dept_mask = _validate_permission_mask_claim(payload["dept_mask"], "dept_mask")
    elif "depts" in payload:
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


def is_document_authorized(
    metadata: dict | None,
    user_role_mask: int,
    user_dept_mask: int,
) -> bool:
    """Fail closed unless both stored permission masks are valid uint32 values."""
    if not isinstance(metadata, dict):
        return False

    doc_role_mask = metadata.get("role_mask")
    doc_dept_mask = metadata.get("dept_mask")
    masks = (doc_role_mask, doc_dept_mask, user_role_mask, user_dept_mask)
    if any(type(mask) is not int or not 0 <= mask <= 0xFFFFFFFF for mask in masks):
        return False

    return is_allowed(doc_role_mask, user_role_mask, doc_dept_mask, user_dept_mask)


def build_qdrant_filter(
    user_role_mask: int,
    user_dept_mask: int,
    knowledge_version_epoch: str,
):
    """
    Build a Qdrant Filter object.

    Qdrant pre-filter 仅处理 status == 'active'（Qdrant Filter 不支持位掩码）。
    RBAC 权限过滤和版本门控在 Python 层通过 is_allowed() 后置执行。

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

    # 1. JWT Bearer token — always attempt decoding. _decode_jwt gates the RS256
    # path on its own JWT_ALGORITHM enable switch (independent of the legacy
    # HS256 secret) and falls back to HS256 only when that secret is enabled.
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        payload = _decode_jwt(token)
        if payload is not None:
            try:
                return _identity_from_jwt(payload)
            except (ValueError, TypeError) as exc:
                # Signature was valid but the authorization claims are malformed, so this
                # token cannot produce an authenticated identity. Fail closed with the
                # same semantics as an unusable token rather than surfacing a 500.
                logger.warning("JWT authorization claims rejected: %s", exc)
                if not cfg.auth.dev_mode:
                    return UserIdentity(user_id="anonymous", user_role_mask=0, user_dept_mask=0)
                logger.warning("Falling back to dev headers after malformed JWT claims")
        else:
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
    except ImportError as _exc_ruf:
        raise RuntimeError("PyJWT is not installed -- cannot generate tokens") from _exc_ruf

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
