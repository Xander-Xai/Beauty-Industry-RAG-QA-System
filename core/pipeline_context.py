"""
管线上下文 - 请求级状态与会话级状态管理

负责在整个在线推理管线中传递请求上下文，包括：
- 用户身份与权限掩码
- 会话状态（对话历史、证据锁定）
- Query Rewrite 结果
- 各阶段中间产物（检索结果、Rerank 分数、Evidence 分数等）

模型定义统一由 common.models 提供，此处 re-export 保持向后兼容。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import ClassVar

from common.models import (
    AnswerGateResult,
    EvidenceGateResult,
    GenerationResult,
    QueryRewriteResult,
    RecallResult,
    RerankResult,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AnswerGateResult",
    "EvidenceGateResult",
    "GenerationResult",
    "QueryRewriteResult",
    "RecallResult",
    "RerankResult",
    "RequestContext",
    "SessionState",
]


@dataclass
class RequestContext:
    """
    请求级上下文 - 在整个管线中传递

    生命周期：单次请求
    用法：
        ctx = RequestContext(user_input="...", user_id="user_001")
        pipeline.process(ctx)
        # 结果存储在 ctx 的各阶段字段中
    """

    # === 输入 ===
    request_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    user_input: str = ""
    image_path: str | None = None
    session_id: str | None = None
    timestamp: float = field(default_factory=time.time)

    # === 用户身份 ===
    user_id: str = ""
    user_role_mask: int = 0
    user_dept_mask: int = 0

    # === Query Rewrite ===
    rewrite_result: QueryRewriteResult | None = None

    # === 检索阶段 ===
    recall_results: list[RecallResult] = field(default_factory=list)
    union_recall_set: list[RecallResult] = field(default_factory=list)
    retrieval_agreement_score: float = 0.0

    # === Rerank 阶段 ===
    rerank_results: list[RerankResult] = field(default_factory=list)

    # === Evidence Gate ===
    evidence_result: EvidenceGateResult | None = None

    # === 生成阶段 ===
    generation_result: GenerationResult | None = None
    max_output_tokens: int = 512
    evidence_locked_doc_ids: list[str] = field(default_factory=list)

    # === Answer Gate ===
    answer_gate_result: AnswerGateResult | None = None

    # === 最终输出 ===
    final_response: str = ""

    # === 性能指标 ===
    stage_timings: dict[str, float] = field(default_factory=dict)
    kv_pressure_at_entry: float = 0.0
    cache_hit_level: str | None = None  # L1 / L2 / MISS
    prefix_cache_hit: bool | None = None  # vLLM Prefix Cache 命中标记
    kv_cache_utilization: float = 0.0  # vLLM KV Cache 利用率（0.0~1.0）

    # === 降级标记 ===
    degraded: bool = False
    fallback_reason: str | None = None

    # === A/B 实验（PRD §12.2） ===
    ab_experiment: str | None = None
    ab_variant: str | None = None

    # === BLIP 在线触发（PRD §6） ===
    blip_triggered: bool = False

    # === CLIP 异步/贡献度指标（PRD §4.5 / GAP-20） ===
    clip_use: bool = False  # CLIP 是否被激活（图像 query 标记）
    clip_top_k: int = 0  # CLIP 召回 top_k（50=全量, 20=降级, 0=跳过）
    clip_sync_timeout: bool = False  # CLIP 同步召回是否超时
    clip_async_hit: bool = False  # CLIP 异步补充召回是否命中
    clip_contribution_ratio: float = 0.0  # CLIP 来源文档在 Rerank Top-K 中的占比

    def record_timing(self, stage: str, duration_ms: float):
        """记录各阶段延迟"""
        self.stage_timings[stage] = duration_ms

    def get_total_latency_ms(self) -> float:
        return sum(self.stage_timings.values())


def _get_redis_session_client():
    """惰性初始化 Redis 会话存储客户端。Redis 不可用时返回 None。"""
    try:
        redis_pw = os.environ.get("REDIS_PASSWORD") or os.environ.get("REDIS_CACHE_PASSWORD", "")
        if not redis_pw:
            return None
        import redis as _redis

        client = _redis.Redis(
            host=os.environ.get("REDIS_CACHE_HOST", "localhost"),
            port=int(os.environ.get("REDIS_CACHE_PORT", 6379)),
            db=int(os.environ.get("REDIS_CACHE_DB", 0)),
            password=redis_pw,
            decode_responses=True,
            socket_connect_timeout=1,
        )
        client.ping()
        logger.info("Redis 会话存储客户端已连接")
        return client
    except Exception:
        return None


# 模块级 Redis 会话客户端（惰性初始化，最多尝试一次）
_redis_client = None
_redis_client_lock = threading.Lock()


def _get_redis_client():
    global _redis_client
    if _redis_client is None:
        with _redis_client_lock:
            if _redis_client is None:
                _redis_client = _get_redis_session_client()
    return _redis_client


@dataclass
class SessionState:
    """
    会话级状态 - 多轮对话管理

    生命周期：用户会话（由 session_id 关联）
    用法：
        state = SessionState.get_or_create(session_id)
        state.add_round(user_input, response)
        state.lock_evidence(doc_ids)
    """

    session_id: str
    dialog_rounds: list[dict] = field(default_factory=list)  # [{user_input, response, rewrite}]
    max_rounds: int = 6  # readme 4.4: 最近6轮
    locked_doc_ids: list[str] = field(default_factory=list)  # 证据锁定
    last_rewrite_result: QueryRewriteResult | None = None
    async_clip_results: list[RecallResult] = field(default_factory=list)  # 异步 CLIP 预热结果
    created_at: float = field(default_factory=time.time)

    # 会话级缓存（全局管理）
    _sessions: ClassVar[dict[str, SessionState]] = {}
    _sessions_lock: ClassVar[threading.Lock] = threading.Lock()

    _SESSION_REDIS_TTL = 7200  # Redis 中会话过期时间（2 小时）

    def add_round(self, user_input: str, response: str, rewrite: QueryRewriteResult | None = None):
        """添加一轮对话"""
        self.dialog_rounds.append(
            {
                "user_input": user_input,
                "response": response,
                "rewrite": rewrite,
            }
        )
        # FIFO 保持最近 N 轮
        if len(self.dialog_rounds) > self.max_rounds:
            self.dialog_rounds = self.dialog_rounds[-self.max_rounds :]
        self.last_rewrite_result = rewrite
        self._persist_to_redis()

    def lock_evidence(self, doc_ids: list[str]):
        """证据锁定 - 续写时禁止重新检索"""
        self.locked_doc_ids = doc_ids
        self._persist_to_redis()

    def get_recent_queries(self, n: int = 6) -> list[str]:
        """获取最近 n 轮的用户查询"""
        return [r["user_input"] for r in self.dialog_rounds[-n:]]

    def store_async_clip_result(self, results: list[RecallResult]):
        """存储异步 CLIP 补充召回结果"""
        self.async_clip_results = results

    def _to_dict(self) -> dict:
        """序列化为 dict（用于 Redis 存储）。"""
        return {
            "session_id": self.session_id,
            "dialog_rounds": self.dialog_rounds,
            "max_rounds": self.max_rounds,
            "locked_doc_ids": self.locked_doc_ids,
            "async_clip_results": self.async_clip_results,
            "created_at": self.created_at,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> SessionState:
        """从 dict 反序列化（从 Redis 读取）。"""
        return cls(
            session_id=data["session_id"],
            dialog_rounds=data.get("dialog_rounds", []),
            max_rounds=data.get("max_rounds", 6),
            locked_doc_ids=data.get("locked_doc_ids", []),
            async_clip_results=data.get("async_clip_results", []),
            created_at=data.get("created_at", time.time()),
        )

    def _persist_to_redis(self):
        """将当前会话状态写入 Redis（异步静默失败）。"""
        try:
            client = _get_redis_client()
            if client is None:
                return
            key = f"session:{self.session_id}"
            client.setex(key, self._SESSION_REDIS_TTL, json.dumps(self._to_dict()))
        except Exception as exc:
            logger.debug("会话 Redis 持久化失败（降级内存）: %s", exc)

    @classmethod
    def get_or_create(cls, session_id: str) -> SessionState:
        """线程安全地获取或创建会话状态（优先 Redis，兜底内存）。"""
        # 尝试从 Redis 读取
        redis_session = cls._try_get_redis(session_id)
        if redis_session is not None:
            return redis_session

        # Redis 不可用或未找到，使用内存存储
        with cls._sessions_lock:
            if session_id not in cls._sessions:
                new_session = cls(session_id=session_id)
                cls._sessions[session_id] = new_session
                # 新创建的会话写入 Redis 预热
                new_session._persist_to_redis()
            return cls._sessions[session_id]

    @classmethod
    def _try_get_redis(cls, session_id: str) -> SessionState | None:
        """尝试从 Redis 获取会话。Redis 不可用或未找到时返回 None。"""
        try:
            client = _get_redis_client()
            if client is None:
                return None
            key = f"session:{session_id}"
            raw = client.get(key)
            if raw is None:
                return None
            data = json.loads(raw)
            session = cls._from_dict(data)
            # 刷新 TTL
            client.expire(key, cls._SESSION_REDIS_TTL)
            return session
        except Exception:
            return None

    @classmethod
    def cleanup_expired(cls, max_age_seconds: int = 3600):
        """清理过期会话（优先 Redis，兜底内存）。"""
        now = time.time()

        # Redis 清理：由 Redis 的 TTL 自动处理，不做额外操作
        try:
            client = _get_redis_client()
            if client is not None:
                # Redis 会话已有 TTL，但此处可以尝试清理半过期的 key
                cursor = 0
                while True:
                    cursor, keys = client.scan(cursor=cursor, match="session:*", count=100)
                    for key in keys:
                        raw = client.get(key)
                        if raw:
                            try:
                                data = json.loads(raw)
                                if now - data.get("created_at", 0) > max_age_seconds:
                                    client.delete(key)
                            except Exception:
                                client.delete(key)
                    if cursor == 0:
                        break
        except Exception as exc:
            logger.debug("Redis 过期会话清理跳过: %s", exc)

        # 内存清理
        with cls._sessions_lock:
            expired = [sid for sid, s in cls._sessions.items() if now - s.created_at > max_age_seconds]
            for sid in expired:
                del cls._sessions[sid]
