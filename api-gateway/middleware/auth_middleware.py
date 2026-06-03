"""
认证中间件 — FastAPI 依赖项.

从请求中提取用户身份信息（JWT / dev-mode headers / 匿名），
供下游路由和限流器使用。
"""

from __future__ import annotations

import os
import sys

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import Request

from common.auth import parse_identity, require_identity
from common.models import UserIdentity


async def get_current_user(request: Request) -> UserIdentity:
    """
    FastAPI 依赖项：提取当前请求的用户身份.

    - 有 JWT token 时解码得到真实身份
    - 开发模式下支持 X-User-* 头部传入
    - 无认证信息时降级为匿名用户

    Returns:
        UserIdentity: 用户身份信息
    """
    return await parse_identity(request)


async def require_current_user(request: Request) -> UserIdentity:
    """
    FastAPI 依赖项：强制要求有效身份（非匿名）.

    在生产模式下，如果无法提取有效身份则返回 401。
    开发模式下允许匿名访问。

    Returns:
        UserIdentity: 用户身份信息

    Raises:
        HTTPException: 401 未授权（生产模式且无有效凭证时）
    """
    return await require_identity(request)
