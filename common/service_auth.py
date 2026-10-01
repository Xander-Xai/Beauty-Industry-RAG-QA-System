"""
微服务间认证模块

使用共享密钥（通过环境变量 SERVICE_AUTH_TOKEN 传入）进行服务间认证。
每个请求需携带 X-Service-Token Header。
"""

import hmac
import logging
import os

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

# 服务间共享令牌，从环境变量读取
_SERVICE_TOKEN = os.environ.get("SERVICE_AUTH_TOKEN", "")
_DEPLOYMENT_MODE = os.environ.get("DEPLOYMENT_MODE", "")


async def verify_service_token(request: Request):
    """
    FastAPI 依赖：验证 X-Service-Token Header。

    生产模式下 SERVICE_AUTH_TOKEN 必须配置，否则拒绝启动。
    开发模式下未配置令牌时跳过验证。
    """
    if not _SERVICE_TOKEN:
        # 生产模式：令牌未配置时必须拒绝
        if _DEPLOYMENT_MODE == "production":
            logger.critical("SERVICE_AUTH_TOKEN 未配置，生产模式下拒绝所有服务间请求")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service authentication not configured",
            )
        # 开发模式：未配置令牌时跳过验证
        logger.warning("SERVICE_AUTH_TOKEN 未配置，服务认证已禁用（仅限开发环境）")
        return

    token = request.headers.get("X-Service-Token", "")
    if not token or not hmac.compare_digest(token, _SERVICE_TOKEN):
        logger.warning("服务认证失败: 缺少或无效的 X-Service-Token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
        )


def get_service_headers() -> dict:
    """获取附加了服务认证令牌的 HTTP Headers（供服务间调用使用）"""
    token = os.environ.get("SERVICE_AUTH_TOKEN", "")
    if token:
        return {"X-Service-Token": token}
    return {}
