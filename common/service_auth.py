"""
微服务间认证模块

使用共享密钥（通过环境变量 SERVICE_AUTH_TOKEN 传入）进行服务间认证。
每个请求需携带 X-Service-Token Header。
"""
import logging
import os
from fastapi import Request, HTTPException, status

logger = logging.getLogger(__name__)

# 服务间共享令牌，从环境变量读取
_SERVICE_TOKEN = os.environ.get("SERVICE_AUTH_TOKEN", "")


async def verify_service_token(request: Request):
    """
    FastAPI 依赖：验证 X-Service-Token Header。

    如果 SERVICE_AUTH_TOKEN 环境变量未设置，则跳过验证（开发模式）。
    """
    if not _SERVICE_TOKEN:
        # 开发模式：未配置令牌时跳过验证
        return

    token = request.headers.get("X-Service-Token", "")
    if not token or token != _SERVICE_TOKEN:
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
