"""
管线上下文 - 请求级状态与会话级状态管理

负责在整个在线推理管线中传递请求上下文，包括：
- 用户身份与权限掩码
- 会话状态（对话历史、证据锁定）
- Query Rewrite 结果
- 各阶段中间产物（检索结果、Rerank 分数、Evidence 分数等）
"""

from __future__ import annotations

import uuid
import time
import threading
from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional


@dataclass
class QueryRewriteResult:
    """Query Rewrite 输出结构（对应 readme 4.4 节）"""
    rewritten_query: str
    business_type: str              # regulation / development / general / short
    intent: str                     # compliance / formulation / ingredient / general
    requires_context: bool
    standardized_entities: list[str] = field(default_factory=list)
    confidence: float = 0.5
    fallback: bool = False


@dataclass
class RecallResult:
    """单路召回结果"""
    doc_id: str
    content: str
    score: float
    source: str                     # dense_bge / bm25_es / clip_visual / rewrite_variant
    metadata: dict = field(default_factory=dict)


@dataclass
class RerankResult:
    """Rerank 阶段结果"""
    doc_id: str
    content: str
    ce_score_a: float = 0.0        # CrossEncoder-A (法规/成分) 分数
    ce_score_b: float = 0.0        # CrossEncoder-B (通用) 分数
    ce_score_ensemble: float = 0.0  # 两模型均分
    bi_score: float = 0.0           # BiEncoder 分数
    nli_score: float = 0.0          # NLI 蕴含分数
    final_score: float = 0.0        # Evidence Gate 综合分数


@dataclass
class EvidenceGateResult:
    """Evidence Ensemble Gate 输出（对应 readme 7.4 节）"""
    evidence_score: float
    ce_top1_score: float
    ce_top3_mean_score: float
    retrieval_agreement_score: float
    doc_consistency_score: float
    decision: str                   # pass / enhanced_generate / reject
    top_docs: list[RerankResult] = field(default_factory=list)


@dataclass
class GenerationResult:
    """LLM 生成结果"""
    answer: str
    model_used: str                 # qwen3-4b / qwen3-14b
    max_tokens: int
    has_more: bool = False
    session_id: Optional[str] = None
    answer_outline: list[str] = field(default_factory=list)


@dataclass
class AnswerGateResult:
    """Answer Gate（NLI 校验）输出"""
    nli_contradiction_score: float
    nli_entailment_score: float
    passed: bool
    warning: bool = False           # contradiction > 0.5 标记警告
    is_regulation: bool = False     # 法规类强制拒答标记


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
    image_path: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    # === 用户身份 ===
    user_id: str = ""
    user_role_mask: int = 0
    user_dept_mask: int = 0

    # === Query Rewrite ===
    rewrite_result: Optional[QueryRewriteResult] = None

    # === 检索阶段 ===
    recall_results: list[RecallResult] = field(default_factory=list)
    union_recall_set: list[RecallResult] = field(default_factory=list)
    retrieval_agreement_score: float = 0.0

    # === Rerank 阶段 ===
    rerank_results: list[RerankResult] = field(default_factory=list)

    # === Evidence Gate ===
    evidence_result: Optional[EvidenceGateResult] = None

    # === 生成阶段 ===
    generation_result: Optional[GenerationResult] = None
    max_output_tokens: int = 512
    evidence_locked_doc_ids: list[str] = field(default_factory=list)

    # === Answer Gate ===
    answer_gate_result: Optional[AnswerGateResult] = None

    # === 最终输出 ===
    final_response: str = ""

    # === 性能指标 ===
    stage_timings: dict[str, float] = field(default_factory=dict)
    kv_pressure_at_entry: float = 0.0
    cache_hit_level: Optional[str] = None  # L1 / L2 / MISS

    # === 降级标记 ===
    degraded: bool = False
    fallback_reason: Optional[str] = None

    # === A/B 实验（PRD §12.2） ===
    ab_experiment: Optional[str] = None
    ab_variant: Optional[str] = None

    # === BLIP 在线触发（PRD §6） ===
    blip_triggered: bool = False

    def record_timing(self, stage: str, duration_ms: float):
        """记录各阶段延迟"""
        self.stage_timings[stage] = duration_ms

    def get_total_latency_ms(self) -> float:
        return sum(self.stage_timings.values())


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
    max_rounds: int = 6                                      # readme 4.4: 最近6轮
    locked_doc_ids: list[str] = field(default_factory=list)  # 证据锁定
    last_rewrite_result: Optional[QueryRewriteResult] = None
    async_clip_results: list[RecallResult] = field(default_factory=list)  # 异步 CLIP 预热结果
    created_at: float = field(default_factory=time.time)

    # 会话级缓存（全局管理）
    _sessions: ClassVar[dict[str, "SessionState"]] = {}
    _sessions_lock: ClassVar[threading.Lock] = threading.Lock()

    def add_round(self, user_input: str, response: str, rewrite: Optional[QueryRewriteResult] = None):
        """添加一轮对话"""
        self.dialog_rounds.append({
            "user_input": user_input,
            "response": response,
            "rewrite": rewrite,
        })
        # FIFO 保持最近 N 轮
        if len(self.dialog_rounds) > self.max_rounds:
            self.dialog_rounds = self.dialog_rounds[-self.max_rounds:]
        self.last_rewrite_result = rewrite

    def lock_evidence(self, doc_ids: list[str]):
        """证据锁定 - 续写时禁止重新检索"""
        self.locked_doc_ids = doc_ids

    def get_recent_queries(self, n: int = 6) -> list[str]:
        """获取最近 n 轮的用户查询"""
        return [r["user_input"] for r in self.dialog_rounds[-n:]]

    def store_async_clip_result(self, results: list[RecallResult]):
        """存储异步 CLIP 补充召回结果"""
        self.async_clip_results = results

    @classmethod
    def get_or_create(cls, session_id: str) -> "SessionState":
        """线程安全地获取或创建会话状态"""
        with cls._sessions_lock:
            if session_id not in cls._sessions:
                cls._sessions[session_id] = cls(session_id=session_id)
            return cls._sessions[session_id]

    @classmethod
    def cleanup_expired(cls, max_age_seconds: int = 3600):
        """清理过期会话（需持有 _sessions_lock 或在单线程上下文中调用）"""
        now = time.time()
        with cls._sessions_lock:
            expired = [sid for sid, s in cls._sessions.items() if now - s.created_at > max_age_seconds]
            for sid in expired:
                del cls._sessions[sid]
