"""
MinIO 客户端封装 — 临时签名 URL 生成.

PRD §10: 资源访问安全
  doc_id → /api/media/{doc_id} → MinIO 临时签名 URL (60s) + 权限二次校验

使用 minio 官方 SDK，不可用时返回空 URL（降级到直接代理）。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

from common.config import get_config_dict

_config = get_config_dict()

_minio_cfg = _config.get("minio", {})
MINIO_ENDPOINT = _minio_cfg.get("endpoint", "minio:9000")
# SEC-2: MinIO 凭证从环境变量读取，不再使用硬编码默认值
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "") or _minio_cfg.get("access_key", "")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "") or _minio_cfg.get("secret_key", "")
MINIO_BUCKET = _minio_cfg.get("bucket", "rag-media")
MINIO_URL_TTL = _minio_cfg.get("signed_url_ttl_seconds", 60)


class MinioClient:
    """
    MinIO 临时签名 URL 客户端.

    职责：
    - 为指定 doc_id 生成有效期 60s 的 presigned GET URL
    - 不可用时降级为空（调用方使用代理路径）
    """

    def __init__(self):
        self._client = None
        self._available = False
        self._try_init()

    def _try_init(self):
        """尝试初始化 MinIO 客户端"""
        try:
            from minio import Minio
            # endpoint 不含 scheme
            endpoint = MINIO_ENDPOINT.replace("http://", "").replace("https://", "")
            secure = MINIO_ENDPOINT.startswith("https")

            self._client = Minio(
                endpoint,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                secure=secure,
            )

            # 确保 bucket 存在
            if not self._client.bucket_exists(MINIO_BUCKET):
                self._client.make_bucket(MINIO_BUCKET)
                logger.info(f"MinIO bucket '{MINIO_BUCKET}' 已创建")

            self._available = True
            logger.info(f"MinIO 客户端初始化完成: endpoint={endpoint}")

        except ImportError:
            logger.info("minio SDK 未安装，签名 URL 功能不可用")
        except Exception as e:
            logger.warning(f"MinIO 初始化失败: {e}")

    def get_presigned_url(self, doc_id: str, object_name: str | None = None) -> str:
        """
        生成 MinIO 临时签名 URL.

        Args:
            doc_id: 文档 ID（用作 object path 的一部分）
            object_name: MinIO 对象名（默认使用 doc_id）

        Returns:
            签名 URL 字符串；不可用时返回空字符串
        """
        if not self._available or self._client is None:
            return ""

        try:
            from datetime import timedelta

            target = object_name or f"media/{doc_id}"
            url = self._client.presigned_get_object(
                MINIO_BUCKET,
                target,
                expires=timedelta(seconds=MINIO_URL_TTL),
            )
            return url

        except Exception as e:
            logger.error(f"生成签名 URL 失败 (doc_id={doc_id}): {e}")
            return ""

    @property
    def is_available(self) -> bool:
        """MinIO 是否可用"""
        return self._available


# 全局单例
_minio_client_instance: MinioClient | None = None


def get_minio_client() -> MinioClient:
    """获取 MinIO 客户端单例"""
    global _minio_client_instance
    if _minio_client_instance is None:
        _minio_client_instance = MinioClient()
    return _minio_client_instance
