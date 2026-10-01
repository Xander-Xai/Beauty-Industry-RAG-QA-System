"""Auth API routes -- login, refresh, user management."""
import logging
import os
import time
from collections import defaultdict

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
from common.auth import is_admin_role_mask
from common.config import get_config

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# H-4: 登录端点速率限制（5 次/分钟/IP）
_login_attempts: dict = defaultdict(list)
_LOGIN_RATE_LIMIT = 5
_LOGIN_RATE_WINDOW = 60.0  # 秒

# Redis 备用速率限制客户端（惰性初始化）
_redis_rate_limiter = None


def _get_redis_rate_limiter():
    """惰性初始化 Redis 速率限制客户端。Redis 不可用时返回 None。"""
    global _redis_rate_limiter
    if _redis_rate_limiter is None:
        try:
            redis_pw = os.environ.get("REDIS_PASSWORD") or os.environ.get("REDIS_CACHE_PASSWORD", "")
            if redis_pw:
                import redis as _redis
                _redis_rate_limiter = _redis.Redis(
                    host=os.environ.get("REDIS_CACHE_HOST", "localhost"),
                    port=int(os.environ.get("REDIS_CACHE_PORT", 6379)),
                    db=int(os.environ.get("REDIS_CACHE_DB", 0)),
                    password=redis_pw,
                    decode_responses=True,
                    socket_connect_timeout=1,
                )
                _redis_rate_limiter.ping()
                logger.info("Redis 速率限制客户端已连接")
        except Exception:
            _redis_rate_limiter = None  # 回退到内存限流
    return _redis_rate_limiter


def _check_redis_rate_limit(ip: str) -> bool:
    """
    尝试使用 Redis 进行速率限制。

    Returns:
        True = 使用 Redis 检查成功，False = Redis 不可用，需回退内存。
    """
    rl = _get_redis_rate_limiter()
    if rl is None:
        return False

    key = f"ratelimit:login:{ip}"
    try:
        current = rl.get(key)
        if current is not None and int(current) >= _LOGIN_RATE_LIMIT:
            raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后重试")
        pipe = rl.pipeline()
        pipe.incr(key, 1)
        pipe.expire(key, int(_LOGIN_RATE_WINDOW))
        pipe.execute()
        return True
    except HTTPException:
        raise
    except Exception:
        return False


def _get_client_ip(request: Request) -> str:
    """获取客户端真实 IP，支持反向代理场景下解析 X-Forwarded-For 头。"""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        # X-Forwarded-For: <client>, <proxy1>, <proxy2>
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _check_login_rate_limit(ip: str):
    """检查登录速率限制（优先 Redis，兜底内存），超限抛出 HTTPException 429。"""
    # 先尝试 Redis（多 worker 共享）
    if _check_redis_rate_limit(ip):
        return

    # Redis 不可用时，回退到内存限流（单 worker 有效）
    now = time.time()
    attempts = _login_attempts[ip]
    _login_attempts[ip] = [t for t in attempts if now - t < _LOGIN_RATE_WINDOW]
    if len(_login_attempts[ip]) >= _LOGIN_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后重试")
    _login_attempts[ip].append(now)

# Lazy singleton
_store: UserStore | None = None


def get_store() -> UserStore:
    global _store
    if _store is None:
        _store = UserStore()
    return _store


def _require_admin_payload(authorization: str | None) -> dict:
    """Validate the bearer token and enforce configured admin access."""
    config = get_jwt_config()
    if not config.enabled:
        raise HTTPException(status_code=503, detail="JWT 认证未配置，管理端点不可用")

    token = extract_token_from_header(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="需要认证")

    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")

    if not is_admin_role_mask(payload.get("role_mask", 0)):
        raise HTTPException(status_code=403, detail="需要管理员权限")

    return payload


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
    roles: list[str] = []
    departments: list[str] = []

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
    roles: list[str]
    departments: list[str]


@router.get("/metadata")
async def auth_metadata():
    """Public UI/auth metadata used by the browser client."""
    cfg = get_config()
    jwt_config = get_jwt_config()
    role_options = cfg.ui.role_options or [
        {
            "key": name,
            "label": name.replace("_", " ").title(),
            "role_mask": mask,
            "dept_mask": cfg.rbac.public_mask,
        }
        for name, mask in cfg.rbac.roles.items()
    ]
    return {
        "app": {
            "title": cfg.ui.app_title or cfg.system.name,
            "subtitle": cfg.ui.subtitle,
            "version": cfg.system.version,
        },
        "auth": {
            "dev_mode": cfg.auth.dev_mode,
            "auth_required": not cfg.auth.dev_mode,
            "jwt_enabled": jwt_config.enabled,
            "login_enabled": jwt_config.enabled,
            "anonymous_user_id": cfg.ui.anonymous_user_id,
        },
        "rbac": {
            "default_role": cfg.ui.default_role,
            "roles": cfg.rbac.roles,
            "departments": cfg.rbac.departments,
            "role_options": role_options,
        },
    }


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request):
    """用户登录，返回 JWT token pair。"""
    # H-4: 登录速率限制（支持反向代理 X-Forwarded-For）
    client_ip = _get_client_ip(request)
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
    _require_admin_payload(authorization)

    store = get_store()
    users = store.list_users()
    return {"users": [u.to_dict() for u in users]}


@router.post("/users")
async def create_user(req: CreateUserRequest, authorization: str = Header(None)):
    """创建新用户。"""
    _require_admin_payload(authorization)

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
    _require_admin_payload(authorization)

    store = get_store()
    user = store.update_user_roles(user_id, req.roles, req.departments)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user.to_dict()
