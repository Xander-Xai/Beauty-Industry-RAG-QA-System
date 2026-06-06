"""Auth API routes -- login, refresh, user management."""
import logging
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, field_validator
from typing import List, Optional

from auth.jwt_auth import (
    create_token_pair, verify_token, extract_token_from_header,
    TokenPair, get_jwt_config,
)
from auth.user_store import UserStore

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# H-4: 登录端点速率限制（5 次/分钟/IP）
_login_attempts: dict = defaultdict(list)
_LOGIN_RATE_LIMIT = 5
_LOGIN_RATE_WINDOW = 60.0  # 秒


def _check_login_rate_limit(ip: str):
    """检查登录速率限制，超限抛出 HTTPException 429。"""
    now = time.time()
    attempts = _login_attempts[ip]
    # 清理过期记录
    _login_attempts[ip] = [t for t in attempts if now - t < _LOGIN_RATE_WINDOW]
    if len(_login_attempts[ip]) >= _LOGIN_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后重试")
    _login_attempts[ip].append(now)

# Lazy singleton
_store: Optional[UserStore] = None


def get_store() -> UserStore:
    global _store
    if _store is None:
        _store = UserStore()
    return _store


class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator('username', 'password')
    @classmethod
    def validate_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('不能为空')
        return v.strip()


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class CreateUserRequest(BaseModel):
    user_id: str
    username: str
    password: str
    display_name: str
    roles: List[str] = []
    departments: List[str] = []

    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError('密码长度至少为 8 个字符')
        if len(v) > 128:
            raise ValueError('密码长度不能超过 128 个字符')
        return v

    @field_validator('user_id', 'username')
    @classmethod
    def validate_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('不能为空')
        return v.strip()


class UpdateRolesRequest(BaseModel):
    roles: List[str]
    departments: List[str]


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request):
    """用户登录，返回 JWT token pair。"""
    # H-4: 登录速率限制
    client_ip = request.client.host if request.client else "unknown"
    _check_login_rate_limit(client_ip)

    store = get_store()
    user = store.authenticate(req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    config = get_jwt_config()
    if not config.enabled:
        raise HTTPException(status_code=503, detail="JWT 认证未配置")

    pair = create_token_pair(user.user_id, user.role_mask, user.dept_mask)
    return LoginResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        user=user.to_dict(),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(req: RefreshRequest):
    """用 refresh token 获取新的 token pair。"""
    payload = verify_token(req.refresh_token, "refresh")
    if payload is None:
        raise HTTPException(status_code=401, detail="Refresh token 无效或已过期")

    store = get_store()
    user = store.get_user(payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")

    return create_token_pair(user.user_id, user.role_mask, user.dept_mask)


@router.get("/users")
async def list_users(authorization: str = Header(None)):
    """列出所有用户（需要 admin 权限）。"""
    # H-8: JWT 禁用时拒绝访问管理端点（不再完全开放）
    config = get_jwt_config()
    if not config.enabled:
        raise HTTPException(status_code=503, detail="JWT 认证未配置，管理端点不可用")
    token = extract_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="需要认证")
    payload = verify_token(token)
    if not payload or (payload.get("role_mask", 0) & 0x01) == 0:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    store = get_store()
    users = store.list_users()
    return {"users": [u.to_dict() for u in users]}


@router.post("/users")
async def create_user(req: CreateUserRequest, authorization: str = Header(None)):
    """创建新用户。"""
    # H-8: JWT 禁用时拒绝访问管理端点
    config = get_jwt_config()
    if not config.enabled:
        raise HTTPException(status_code=503, detail="JWT 认证未配置，管理端点不可用")
    token = extract_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="需要认证")
    payload = verify_token(token)
    if not payload or (payload.get("role_mask", 0) & 0x01) == 0:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    store = get_store()
    try:
        user = store.create_user(
            req.user_id, req.username, req.password,
            req.display_name, req.roles, req.departments,
        )
        return user.to_dict()
    except Exception as e:
        logger.error("创建用户失败: %s", e)
        raise HTTPException(status_code=400, detail="创建用户失败，请检查参数后重试")


@router.put("/users/{user_id}/roles")
async def update_user_roles(user_id: str, req: UpdateRolesRequest, authorization: str = Header(None)):
    """更新用户角色。"""
    config = get_jwt_config()
    if not config.enabled:
        raise HTTPException(status_code=503, detail="JWT 认证未配置，管理端点不可用")
    token = extract_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="需要认证")
    payload = verify_token(token)
    if not payload or (payload.get("role_mask", 0) & 0x01) == 0:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    store = get_store()
    user = store.update_user_roles(user_id, req.roles, req.departments)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user.to_dict()
