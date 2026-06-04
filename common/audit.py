"""
审计日志模块（readme §11）

功能：
1. 记录用户角色掩码、过滤表达式、拦截原因
2. user_query 强制 SHA256 哈希（不记录明文）
3. 研发配方类查询（business_type="development"）脱敏为 [REDACTED]
"""
import hashlib
import logging
import json
import time
from typing import Optional

logger = logging.getLogger("audit")


def hash_query(query: str) -> str:
    """对用户查询进行 SHA256 哈希，前 16 位用于日志"""
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


def redact_query(query: str, business_type: str = "") -> str:
    """
    查询脱敏：
    - development 类型查询完全脱敏为 [REDACTED]
    - 其他类型返回 SHA256 哈希
    """
    if business_type == "development":
        return "[REDACTED]"
    return hash_query(query)


def log_audit_event(
    event_type: str,           # query_received / admission_rejected / cache_hit / etc.
    request_id: str,
    user_id: str = "",
    user_role_mask: int = 0,
    query: str = "",
    business_type: str = "",
    filter_expr: str = "",
    reject_reason: str = "",
    extra: Optional[dict] = None,
):
    """
    记录审计事件。

    所有 user_query 均通过 hash_query() 或 redact_query() 处理后才写入日志。
    """
    event = {
        "ts": time.time(),
        "event": event_type,
        "request_id": request_id,
        "user_id": user_id,
        "user_role_mask": user_role_mask,
        "query_hash": redact_query(query, business_type) if query else "",
        "business_type": business_type,
        "filter_expr": filter_expr,
        "reject_reason": reject_reason,
    }
    if extra:
        event.update(extra)

    # 使用 INFO 级别记录审计事件
    logger.info(json.dumps(event, ensure_ascii=False))
