"""
FastAPI 依赖注入模块

从请求 Header 中解析用户身份信息，提供给路由处理器使用。
安全说明：在生产模式下（dev_mode=False），X-User-* Header 不被信任，
仅 JWT Bearer token 用于身份验证。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)

# 默认角色掩码：不给任何特权（零掩码 = 公开访问）
_DEFAULT_ROLE_MASK = 0


@dataclass
class RequestIdentity:
    """从请求中解析出的用户身份"""

    user_id: str = "anonymous"
    user_role_mask: int = 0  # public (所有位为 0 = 无权限 = 公开访问)
    user_dept_mask: int = 0  # all departments


def _get_dev_mode() -> bool:
    """延迟获取 dev_mode 配置，避免循环导入"""
    try:
        from common.config import get_config
        return get_config().auth.dev_mode
    except Exception:
        return False  # 默认不信任 header


def get_identity(
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    x_role_mask: str | None = Header(default=None, alias="X-Role-Mask"),
    x_dept_mask: str | None = Header(default=None, alias="X-Dept-Mask"),
) -> RequestIdentity:
    """
    FastAPI 依赖函数：从 Header 解析用户身份。

    注意：此函数仅用于 monolith 模式的简单身份提取。
    微服务模式应使用 common/auth.py 的 parse_identity()。
    Header 中的权限信息仅在 dev_mode 下可信。
    """
    identity = RequestIdentity()

    # 生产模式下：忽略 Header 中的权限信息，仅提取 user_id
    if not _get_dev_mode():
        if x_user_id is not None:
            identity.user_id = x_user_id
        # 生产模式下 Header 权限掩码不可信，保持默认零掩码
        return identity

    # 开发模式下：完整读取 Header（方便本地调试）
    if x_user_id is not None:
        identity.user_id = x_user_id

    if x_role_mask is not None:
        try:
            identity.user_role_mask = int(x_role_mask)
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid X-Role-Mask header: {x_role_mask!r}")
            raise HTTPException(
                status_code=400,
                detail=f"X-Role-Mask 必须为整数，收到: {x_role_mask!r}",
            ) from e

    if x_dept_mask is not None:
        try:
            identity.user_dept_mask = int(x_dept_mask)
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid X-Dept-Mask header: {x_dept_mask!r}")
            raise HTTPException(
                status_code=400,
                detail=f"X-Dept-Mask 必须为整数，收到: {x_dept_mask!r}",
            ) from e

    return identity
