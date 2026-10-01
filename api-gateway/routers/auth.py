"""Auth API routes for the API Gateway — login, refresh, metadata, user management."""

import logging
import os
import sys
import time
from collections import defaultdict

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, field_validator

from auth.jwt_auth import (
    TokenPair,
    create_token_pair,
    extract_token_from_header,
    get_jwt_config,
    verify_token,
)
from auth.user_store import UserStore
from common.config import get_config

router = APIRouter()
logger = logging.getLogger(__name__)

# Login rate limit (5 attempts/min/IP)
_login_attempts: dict = defaultdict(list)
_LOGIN_RATE_LIMIT = 5
_LOGIN_RATE_WINDOW = 60.0


def _check_login_rate_limit(ip: str):
    now = time.time()
    attempts = _login_attempts[ip]
    _login_attempts[ip] = [t for t in attempts if now - t < _LOGIN_RATE_WINDOW]
    if len(_login_attempts[ip]) >= _LOGIN_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Login attempts too frequent")


_store: UserStore | None = None


def get_store() -> UserStore:
    global _store
    if _store is None:
        _store = UserStore()
    return _store


class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username", "password")
    @classmethod
    def validate_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # noqa: S105 -- protocol constant, not a credential
    expires_in: int
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class CreateUserRequest(BaseModel):
    user_id: str
    username: str
    password: str
    display_name: str
    roles: list[str] = []
    departments: list[str] = []

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v

    @field_validator("user_id", "username")
    @classmethod
    def validate_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()


class UpdateRolesRequest(BaseModel):
    roles: list[str]
    departments: list[str]


@router.get("/api/auth/metadata")
async def auth_metadata():
    """Public UI/auth metadata for the browser client."""
    cfg = get_config()
    jwt_config = get_jwt_config()
    role_options = getattr(cfg.ui, "role_options", None) or [
        {
            "key": name,
            "label": name.replace("_", " ").title(),
            "role_mask": mask,
            "dept_mask": getattr(cfg.rbac, "public_mask", 0),
        }
        for name, mask in cfg.rbac.roles.items()
    ]
    return {
        "app": {
            "title": getattr(cfg.ui, "app_title", None) or cfg.system.name,
            "subtitle": getattr(cfg.ui, "subtitle", ""),
            "version": cfg.system.version,
        },
        "auth": {
            "dev_mode": cfg.auth.dev_mode,
            "jwt_enabled": jwt_config.enabled,
            "anonymous_user_id": getattr(cfg.ui, "anonymous_user_id", "web-user"),
        },
        "rbac": {
            "default_role": getattr(cfg.ui, "default_role", ""),
            "roles": cfg.rbac.roles,
            "departments": cfg.rbac.departments,
            "role_options": role_options,
        },
    }


@router.post("/api/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request):
    """User login, returns JWT token pair."""
    client_ip = request.client.host if request.client else "unknown"
    _check_login_rate_limit(client_ip)

    store = get_store()
    user = store.authenticate(req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    config = get_jwt_config()
    if not config.enabled:
        raise HTTPException(status_code=503, detail="JWT authentication not configured")

    pair = create_token_pair(user.user_id, user.role_mask, user.dept_mask)
    return LoginResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        user=user.to_dict(),
    )


@router.post("/api/auth/refresh", response_model=TokenPair)
async def refresh(req: RefreshRequest):
    """Refresh token to get a new token pair."""
    payload = verify_token(req.refresh_token, "refresh")
    if payload is None:
        raise HTTPException(status_code=401, detail="Refresh token invalid or expired")

    store = get_store()
    user = store.get_user(payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or disabled")

    return create_token_pair(user.user_id, user.role_mask, user.dept_mask)


@router.get("/api/auth/users")
async def list_users(authorization: str = Header(None)):
    """List all users (admin only)."""
    config = get_jwt_config()
    if not config.enabled:
        raise HTTPException(status_code=503, detail="JWT not configured")
    token = extract_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = verify_token(token)
    if not payload or (payload.get("role_mask", 0) & 0x01) == 0:
        raise HTTPException(status_code=403, detail="Admin privileges required")

    store = get_store()
    users = store.list_users()
    return {"users": [u.to_dict() for u in users]}


@router.post("/api/auth/users")
async def create_user(req: CreateUserRequest, authorization: str = Header(None)):
    """Create a new user (admin only)."""
    config = get_jwt_config()
    if not config.enabled:
        raise HTTPException(status_code=503, detail="JWT not configured")
    token = extract_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = verify_token(token)
    if not payload or (payload.get("role_mask", 0) & 0x01) == 0:
        raise HTTPException(status_code=403, detail="Admin privileges required")

    store = get_store()
    try:
        user = store.create_user(
            req.user_id,
            req.username,
            req.password,
            req.display_name,
            req.roles,
            req.departments,
        )
        return user.to_dict()
    except Exception as e:
        logger.error("Failed to create user: %s", e)
        raise HTTPException(status_code=400, detail="Failed to create user") from e


@router.put("/api/auth/users/{user_id}/roles")
async def update_user_roles(user_id: str, req: UpdateRolesRequest, authorization: str = Header(None)):
    """Update user roles (admin only)."""
    config = get_jwt_config()
    if not config.enabled:
        raise HTTPException(status_code=503, detail="JWT not configured")
    token = extract_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = verify_token(token)
    if not payload or (payload.get("role_mask", 0) & 0x01) == 0:
        raise HTTPException(status_code=403, detail="Admin privileges required")

    store = get_store()
    user = store.update_user_roles(user_id, req.roles, req.departments)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user.to_dict()
