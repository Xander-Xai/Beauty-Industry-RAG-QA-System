"""
审计日志模块（readme §11）

功能：
1. 记录用户角色掩码、过滤表达式、拦截原因
2. user_query 强制 SHA256 哈希（不记录明文）
3. 研发配方类查询（business_type="development"）脱敏为 [REDACTED]
4. 审计日志持久化：Redis Stream + 文件 JSONL 双 Sink
"""
import hashlib
import json
import logging
import os
import threading
import time

logger = logging.getLogger("audit")

# ── 审计 Sink 管理 ────────────────────────────────────────────────────────

_sinks: list = []
_sinks_lock = threading.Lock()

# Redis Stream sink 缓存
_redis_client = None
_redis_init_done = False


def _init_sinks():
    """延迟初始化审计 Sink（首次写入时调用）"""
    global _sinks, _redis_init_done
    if _redis_init_done:
        return
    _redis_init_done = True

    try:
        import redis as _redis_lib
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        state_cfg = cfg.get("redis", {}).get("state", {})
        _redis_client = _redis_lib.Redis(
            host=state_cfg.get("host", "redis"),
            port=state_cfg.get("port", 6379),
            db=state_cfg.get("db", 1),
            decode_responses=True,
            socket_connect_timeout=2,
        )
        _redis_client.ping()
        _sinks.append("redis_stream")
        logger.info("审计日志 Redis Stream sink 初始化成功")
    except Exception as e:
        logger.debug(f"审计日志 Redis Stream sink 不可用: {e}")

    # 文件 JSONL sink 始终可用
    _sinks.append("file_jsonl")


def _write_redis_stream(event: dict):
    """将审计事件同时写入 Redis Stream 和 SortedSet（双写保证查询兼容）"""
    global _redis_client
    if _redis_client is None:
        return
    try:
        event_json = json.dumps(event, ensure_ascii=False)
        ts = event.get("ts", time.time())
        # 写入 Stream（保留最近 10000 条，防止无限增长）
        _redis_client.xadd(
            "audit:events",
            {"data": event_json},
            maxlen=10000,
        )
        # 写入 SortedSet（用于时间范围查询）
        _redis_client.zadd("rag:audit_logs", {event_json: ts})
        # 清理 30 天前的数据
        cutoff = ts - 30 * 86400
        _redis_client.zremrangebyscore("rag:audit_logs", 0, cutoff)
    except Exception as e:
        logger.debug(f"Redis 审计日志写入失败: {e}")


def _write_file_jsonl(event: dict):
    """将审计事件按日期写入 JSONL 文件"""
    try:
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "audit")
        os.makedirs(log_dir, exist_ok=True)
        date_str = time.strftime("%Y-%m-%d", time.localtime(event.get("ts", time.time())))
        fpath = os.path.join(log_dir, f"{date_str}.jsonl")
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"审计日志文件写入失败: {e}")


def _dispatch_to_sinks(event: dict):
    """将审计事件分发到所有已初始化的 Sink"""
    _init_sinks()
    for sink in _sinks:
        if sink == "redis_stream":
            _write_redis_stream(event)
        elif sink == "file_jsonl":
            _write_file_jsonl(event)


def query_audit_events(
    start_time: float | None = None,
    end_time: float | None = None,
    user_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """
    查询已持久化的审计日志。

    优先从 Redis Stream 查询（生产环境），回退到文件 JSONL 查询。

    Args:
        start_time: 起始时间戳
        end_time: 结束时间戳
        user_id: 按用户 ID 过滤
        event_type: 按事件类型过滤
        limit: 最大返回条数

    Returns:
        list[dict]: 审计事件列表
    """
    _init_sinks()
    results = []

    # 尝试从 Redis 查询（同时支持 Stream 和 SortedSet 两种格式）
    if _redis_client is not None and "redis_stream" in _sinks:
        try:
            # 优先从 SortedSet 查询（新数据写入 SortedSet）
            min_score = start_time or 0
            max_score = end_time or "+inf"
            raw_logs = _redis_client.zrangebyscore(
                "rag:audit_logs", min_score, max_score, start=0, num=limit * 2
            )
            for raw in raw_logs:
                try:
                    event = json.loads(raw)
                    if user_id and event.get("user_id") != user_id:
                        continue
                    if event_type and event.get("event") != event_type:
                        continue
                    results.append(event)
                    if len(results) >= limit:
                        break
                except (json.JSONDecodeError, TypeError):
                    continue
            if results:
                return results[:limit]

            # SortedSet 无数据时，回退到 Stream 查询（兼容旧数据）
            stream_entries = _redis_client.xrevrange(
                "audit:events", count=limit * 2,
            )
            for entry_id, fields in stream_entries:
                try:
                    event = json.loads(fields.get("data", "{}"))
                    ts = event.get("ts", 0)
                    if start_time and ts < start_time:
                        continue
                    if end_time and ts > end_time:
                        continue
                    if user_id and event.get("user_id") != user_id:
                        continue
                    if event_type and event.get("event") != event_type:
                        continue
                    results.append(event)
                    if len(results) >= limit:
                        break
                except (json.JSONDecodeError, TypeError):
                    continue
            if results:
                return results[:limit]
        except Exception as e:
            logger.debug(f"Redis 审计日志查询失败: {e}")

    # 回退到文件 JSONL 查询
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "audit")
    if not os.path.exists(log_dir):
        return results

    for fname in sorted(os.listdir(log_dir), reverse=True):
        if not fname.endswith(".jsonl"):
            continue
        fpath = os.path.join(log_dir, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        ts = event.get("ts", 0)
                        if start_time and ts < start_time:
                            continue
                        if end_time and ts > end_time:
                            continue
                        if user_id and event.get("user_id") != user_id:
                            continue
                        if event_type and event.get("event") != event_type:
                            continue
                        results.append(event)
                        if len(results) >= limit:
                            return results
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning(f"审计日志文件读取失败 {fpath}: {e}")

    return results


def get_audit_events(
    start_time: float | None = None,
    end_time: float | None = None,
    limit: int = 100,
) -> list[dict]:
    """
    PRD §11: 查询审计事件（简化接口）。

    Args:
        start_time: 起始时间戳
        end_time: 结束时间戳
        limit: 最大返回条数（默认 100）

    Returns:
        list[dict]: 审计事件列表
    """
    return query_audit_events(start_time=start_time, end_time=end_time, limit=limit)


def normalize_query(query: str) -> str:
    """PRD §10.2: 查询标准化 — 大小写统一、多余空格/标点清理"""
    import re as _re
    q = query.strip()
    q = _re.sub(r'\s+', ' ', q)  # 多余空白合并
    q = _re.sub(r'[？！。，、；：]', lambda m: {
        '？': '?', '！': '!', '。': '.', '，': ',', '、': ',', '；': ';', '：': ':'
    }.get(m.group(), m.group()), q)  # 中文标点统一为英文
    return q.lower()

def hash_query(query: str) -> str:
    """对用户查询进行 SHA256 哈希（先标准化），前 16 位用于日志"""
    normalized = normalize_query(query)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


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
    extra: dict | None = None,
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

    # PRD §11: 审计日志持久化（Redis Stream + 文件 JSONL）
    _dispatch_to_sinks(event)
