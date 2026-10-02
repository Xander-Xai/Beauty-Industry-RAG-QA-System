"""
审计日志模块（readme §11 + 企业动作审计）

功能：
1. 记录用户角色掩码、过滤表达式、拦截原因
2. user_query 强制 SHA256 哈希（不记录明文）
3. 研发配方类查询（business_type="development"）脱敏为 [REDACTED]
4. 审计日志持久化：Redis Stream + 文件 JSONL 双 Sink
5. 企业动作事件（:func:`audit_event`）：登录成功/失败/限流、用户与角色管理、
   受保护媒体访问拒绝、epoch 封存。带统一 schema 与强制脱敏。

两条事件流的区别（刻意并存，不是重复）：

* :func:`log_audit_event` 记录 **查询级** 事件（intent、filter_expr、
  admission 拒绝原因），沿用 readme §11 的 schema 与双 Sink 持久化。
* :func:`audit_event` 记录 **企业动作级** 事件，回答审计问题：谁做了什么
  动作、改了哪个资源、是否被允许、为什么被拒。schema 稳定（见
  :class:`AuditEvent` 的核心 9 字段），额外上下文只能进 ``metadata``。

所有出口都在写入前经过 :func:`redact`，因此调用方即使忘记脱敏也不会泄漏凭据。
持久化复用同一套 Redis Stream + JSONL Sink，外加 stdout 结构化日志，
便于生产部署转发到集中式日志 / SIEM；本模块不实现日志投递。
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field
from typing import Any

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
            raw_logs = _redis_client.zrangebyscore("rag:audit_logs", min_score, max_score, start=0, num=limit * 2)
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
                "audit:events",
                count=limit * 2,
            )
            for _entry_id, fields in stream_entries:
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
    q = _re.sub(r"\s+", " ", q)  # 多余空白合并
    q = _re.sub(
        r"[？！。，、；：]",
        lambda m: {"？": "?", "！": "!", "。": ".", "，": ",", "、": ",", "；": ";", "：": ":"}.get(
            m.group(), m.group()
        ),
        q,
    )  # 中文标点统一为英文
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
    event_type: str,  # query_received / admission_rejected / cache_hit / etc.
    request_id: str = "",
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

    ``request_id`` 省略时回退到当前请求上下文（见 :func:`get_request_id`），
    使调用方不必手工传递就能与访问日志、trace span 关联。
    ``extra`` 同样经过 :func:`redact`，与新事件流共用同一套脱敏规则。
    """
    event = {
        "ts": time.time(),
        "event": event_type,
        "request_id": request_id or get_request_id(),
        "user_id": user_id,
        "user_role_mask": user_role_mask,
        "query_hash": redact_query(query, business_type) if query else "",
        "business_type": business_type,
        "filter_expr": filter_expr,
        "reject_reason": reject_reason,
    }
    if extra:
        event.update(redact(extra))

    # 使用 INFO 级别记录审计事件
    logger.info(json.dumps(event, ensure_ascii=False))

    # PRD §11: 审计日志持久化（Redis Stream + 文件 JSONL）
    _dispatch_to_sinks(event)


# ═══════════════════════════════════════════════════════════════════════
# 企业动作审计事件（Enterprise action audit events）
# ═══════════════════════════════════════════════════════════════════════

# ── 关联 ID ────────────────────────────────────────────────────────────────

#: 由 RequestLoggingMiddleware 设置，使审计事件、访问日志与 trace span 引用同一
#: 个 request_id，而不必把它穿过每一个函数签名。
_current_request_id: ContextVar[str] = ContextVar("rag_request_id", default="")


#: Longest accepted inbound correlation id. An inbound X-Request-ID is
#: attacker-controlled and this value reaches the access log, every audit event,
#: the Redis Stream, the daily JSONL file and the response header, so an
#: unbounded string would let a client flood all of them with one request.
MAX_REQUEST_ID_LENGTH = 64

#: Only these characters are accepted. Everything else (CR, LF, TAB, NUL,
#: other control characters, spaces) is rejected so an id cannot forge a log line
#: or split a response header.
_REQUEST_ID_ALLOWED_RE = re.compile(rf"\A[A-Za-z0-9_.:-]{{1,{MAX_REQUEST_ID_LENGTH}}}\Z")


def sanitize_request_id(candidate: str | None) -> str:
    """Return a safe correlation id, generating one when the input is unusable.

    An inbound id is only honoured when it is short and made solely of
    characters that cannot affect a log line or a header. Anything else is
    discarded in favour of a freshly generated id, so a malformed or hostile
    value can never be propagated into an audit record.
    """
    if candidate and _REQUEST_ID_ALLOWED_RE.match(candidate):
        return candidate
    return new_request_id()


def new_request_id() -> str:
    return str(uuid.uuid4())


def set_request_id(request_id: str) -> Token:
    return _current_request_id.set(request_id)


def reset_request_id(token: Token) -> None:
    _current_request_id.reset(token)


def get_request_id() -> str:
    """当前 request_id；请求之外（CLI、后台任务）返回 ``""``。"""
    return _current_request_id.get()


# ── 规范动作名 ──────────────────────────────────────────────────────────────
#
# 只列出真实代码路径上存在的动作。这里没有 `knowledge.epoch.activate`：
# 本仓库不存在 activate 接口，epoch 激活是一次显式人工改配置的行为，
# 为它虚构一个审计事件等于宣称存在一个并不存在的 API。

ACTION_LOGIN_SUCCESS = "auth.login.success"
ACTION_LOGIN_FAILURE = "auth.login.failure"
ACTION_LOGIN_RATE_LIMITED = "auth.login.rate_limited"
ACTION_USER_CREATE = "admin.user.create"
ACTION_ROLE_UPDATE = "admin.role.update"
ACTION_MEDIA_ACCESS_DENIED = "media.access.denied"
ACTION_EPOCH_SEAL = "knowledge.epoch.seal"

KNOWN_ACTIONS = frozenset(
    {
        ACTION_LOGIN_SUCCESS,
        ACTION_LOGIN_FAILURE,
        ACTION_LOGIN_RATE_LIMITED,
        ACTION_USER_CREATE,
        ACTION_ROLE_UPDATE,
        ACTION_MEDIA_ACCESS_DENIED,
        ACTION_EPOCH_SEAL,
    }
)

OUTCOME_SUCCESS = "success"
OUTCOME_DENIED = "denied"
OUTCOME_FAILED = "failed"
KNOWN_OUTCOMES = frozenset({OUTCOME_SUCCESS, OUTCOME_DENIED, OUTCOME_FAILED})

#: 认证成功前的行为主体。刻意与真实 user_id 区分，避免把未认证事件
#: 误认为已识别身份。
ANONYMOUS_ACTOR = "anonymous"

REDACTED = "[REDACTED]"

_KEY_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")

#: 与「分隔符剥离 + 小写」后的 key 做子串匹配。
_SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "apikey",
    "credential",
    "privatekey",
    "passphrase",
)

#: 只作为 normalized key 的 **后缀** 匹配。`auth` 必须是后缀而非子串：
#: 子串匹配会把 `auth_env_presence`（一个存在性映射）也压成常量，
#: 这正是本项目此前在 provenance 侧踩过的坑。
_SECRET_KEY_SUFFIXES = ("authorization", "authtoken", "auth", "jwt", "signature")


def _normalized_key(key: Any) -> str:
    """小写并去掉分隔符，使拼写变体归一。

    ``api_key`` / ``apiKey`` / ``api-key`` 都归一为 ``apikey``。
    """
    return _KEY_SEPARATOR_RE.sub("", str(key).lower())


def is_secret_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    if not normalized:
        return False
    if normalized.endswith(_SECRET_KEY_SUFFIXES):
        return True
    return any(marker in normalized for marker in _SECRET_KEY_MARKERS)


_BEARER_RE = re.compile(r"^\s*bearer\s+\S", re.IGNORECASE)
_JWT_RE = re.compile(r"^ey[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*$")


def _looks_like_bearer(value: str) -> bool:
    """检测以 **值** 形态出现的凭据。

    基于 key 的脱敏在调用方把裸 token 放进 ``detail`` 之类字段时无能为力，
    因此还要检查值的形状。
    """
    return bool(_BEARER_RE.match(value) or _JWT_RE.match(value))


def redact(value: Any, _depth: int = 0) -> Any:
    """递归把疑似凭据的值替换为 ``[REDACTED]``。

    遍历任意深度的 dict / list / tuple，因此藏在无害 key 下的凭据
    （``{"meta": {"apiKey": ...}}``）无法存活。
    """
    if _depth > 12:
        return REDACTED
    if isinstance(value, dict):
        return {key: (REDACTED if is_secret_key(key) else redact(item, _depth + 1)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth + 1) for item in value]
    if isinstance(value, str) and _looks_like_bearer(value):
        return REDACTED
    return value


@dataclass
class AuditEvent:
    """一条结构化审计记录。

    前 9 个字段是稳定核心。额外上下文只能进 ``metadata``，而不是不断新增
    顶层字段，这样消费方可以依赖核心 schema 不变。
    """

    timestamp: float
    request_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, default=str)


def audit_event(
    *,
    action: str,
    outcome: str,
    actor_id: str | None = None,
    resource_type: str = "",
    resource_id: str | None = None,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    """构造并发出��条企业动作审计事件；返回事件对象以便测试断言。

    未登记的 action / outcome 会被拒绝而不是静默写入：动作名拼错会生成一个
    没有任何消费者知道该如何告警的、实际上无人审计的动作。
    """
    if action not in KNOWN_ACTIONS:
        raise ValueError(f"unknown audit action {action!r}; register it in KNOWN_ACTIONS first")
    if outcome not in KNOWN_OUTCOMES:
        raise ValueError(f"unknown audit outcome {outcome!r}")

    event = AuditEvent(
        timestamp=round(time.time(), 6),
        request_id=request_id if request_id is not None else get_request_id(),
        actor_id=actor_id or ANONYMOUS_ACTOR,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id if resource_id is not None else "",
        outcome=outcome,
        reason=reason,
        metadata=redact(metadata or {}),
    )
    _emit_audit_event(event)
    return event


def _emit_audit_event(event: AuditEvent) -> None:
    """写入结构化日志，并复用既有的 Redis Stream + JSONL 持久化。"""
    payload = event.to_dict()
    if event.outcome == OUTCOME_SUCCESS:
        logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    elif event.outcome == OUTCOME_DENIED:
        logger.warning(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    else:
        logger.error(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    _dispatch_to_sinks(payload)


# ── 真实调用点的便捷封装 ────────────────────────────────────────────────────


def audit_login_success(user_id: str, metadata: dict[str, Any] | None = None) -> AuditEvent:
    return audit_event(
        action=ACTION_LOGIN_SUCCESS,
        outcome=OUTCOME_SUCCESS,
        actor_id=user_id,
        resource_type="session",
        resource_id=user_id,
        metadata=metadata,
    )


def audit_login_failure(username: str, reason: str, metadata: dict[str, Any] | None = None) -> AuditEvent:
    return audit_event(
        action=ACTION_LOGIN_FAILURE,
        outcome=OUTCOME_DENIED,
        actor_id=ANONYMOUS_ACTOR,
        resource_type="session",
        resource_id=username,
        reason=reason,
        metadata=metadata,
    )


def audit_login_rate_limited(client_ip: str, metadata: dict[str, Any] | None = None) -> AuditEvent:
    return audit_event(
        action=ACTION_LOGIN_RATE_LIMITED,
        outcome=OUTCOME_DENIED,
        actor_id=ANONYMOUS_ACTOR,
        resource_type="session",
        resource_id=client_ip,
        reason="login rate limit exceeded",
        metadata=metadata,
    )


def audit_media_denied(actor_id: str, doc_id: str, reason: str) -> AuditEvent:
    return audit_event(
        action=ACTION_MEDIA_ACCESS_DENIED,
        outcome=OUTCOME_DENIED,
        actor_id=actor_id,
        resource_type="media",
        resource_id=doc_id,
        reason=reason,
    )


def configure_audit_logging(level: int = logging.INFO, stream: Any = None) -> None:
    """为 audit logger 挂一个 JSON stream handler。

    刻意保持最小：一个 formatter，不自建日志框架。生产部署直接重定向 stdout，
    或把同一个 logger 指向 rotating file handler，再向外转发。
    """
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
