"""
FastAPI 依赖注入模块

从请求 Header 中解析用户身份信息，提供给路由处理器使用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)


@dataclass
class RequestIdentity:
    """从请求中解析出的用户身份"""

    user_id: str = "anonymous"
    user_role_mask: int = 0  # public (所有位为 0 = 无权限 = 公开访问)
    user_dept_mask: int = 0  # all departments


def get_identity(
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    x_role_mask: str | None = Header(default=None, alias="X-Role-Mask"),
    x_dept_mask: str | None = Header(default=None, alias="X-Dept-Mask"),
) -> RequestIdentity:
    """
    FastAPI 依赖函数：从 Header 解析用户身份。

    Header 规范：
        X-User-ID    : 用户标识符（字符串）
        X-Role-Mask  : 角色位掩码（整数，可选）
        X-Dept-Mask  : 部门位掩码（整数，可选）

    默认值：
        user_id     = "anonymous"
        role_mask   = 0        (public，无特殊权限)
        dept_mask   = 0        (all，不限制部门)

    Raises:
        HTTPException 400: 当 role_mask / dept_mask 无法转换为整数时。
    """
    identity = RequestIdentity()

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
