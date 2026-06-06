"""
Pydantic 请求/响应模型

所有 HTTP 接口的数据契约，确保类型安全与自动文档生成。
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# ─── Request 模型 ──────────────────────────────────────────


class QueryRequest(BaseModel):
    """单轮查询请求"""

    query: str = Field(..., min_length=1, max_length=2000, description="用户查询文本")
    session_id: Optional[str] = Field(None, max_length=64, description="会话 ID（可选）")
    user_id: Optional[str] = Field(None, max_length=64, description="用户 ID（可选，优先使用 Header）")
    # PRD §6: 仅支持离线已向量化图像，不支持用户实时上传图片在线解析
    image_path: Optional[str] = Field(None, max_length=512, description="已弃用：系统不支持实时图片上传")


class ChatRequest(BaseModel):
    """多轮对话请求"""

    message: str = Field(..., min_length=1, max_length=2000, description="用户消息文本")
    session_id: str = Field(..., min_length=1, max_length=64, description="会话 ID（必填）")


# ─── Response 模型 ─────────────────────────────────────────


class QueryResponse(BaseModel):
    """单轮查询响应"""

    answer: str = Field(..., description="RAG 生成的回答")
    session_id: Optional[str] = Field(None, description="会话 ID")
    business_type: Optional[str] = Field(None, description="业务类型: regulation/development/general/short")
    intent: Optional[str] = Field(None, description="用户意图: compliance/formulation/ingredient/general")
    evidence_doc_ids: list[str] = Field(default_factory=list, description="Evidence Gate 锁定的文档 ID 列表")
    latency_ms: float = Field(..., description="端到端延迟（毫秒）")
    cache_hit: bool = Field(False, description="是否命中缓存（L1 或 L2）")


class ChatMessage(BaseModel):
    """对话历史中的单条消息"""

    role: str = Field(..., description="角色: user 或 assistant")
    content: str = Field(..., description="消息内容")


class ChatResponse(BaseModel):
    """多轮对话响应"""

    answer: str = Field(..., description="RAG 生成的回答")
    session_id: str = Field(..., description="会话 ID")
    history: list[ChatMessage] = Field(default_factory=list, description="完整对话历史（最近 6 轮）")


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str = Field(..., description="整体状态: healthy / degraded")
    version: str = Field("2.0.0", description="系统版本号")
    dependencies: dict[str, bool] = Field(
        default_factory=dict,
        description="各依赖服务连通状态",
    )


class LatencyPercentiles(BaseModel):
    """延迟百分位数"""

    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    count: int = 0


class CacheHitRate(BaseModel):
    """缓存命中率"""

    L1: float = 0.0
    L2: float = 0.0


class StatsResponse(BaseModel):
    """系统统计响应"""

    uptime_seconds: float = Field(..., description="系统运行时长（秒）")
    cache_hit_rate: CacheHitRate = Field(default_factory=CacheHitRate, description="缓存命中率")
    rewrite_fallback_rate: float = Field(0.0, description="Query Rewrite 降级率")
    latency_percentiles: dict[str, LatencyPercentiles] = Field(
        default_factory=dict,
        description="各阶段延迟百分位数",
    )
    kv_pressure: float = Field(0.0, description="KV Cache 压力值（0~1）")
    active_requests: int = Field(0, description="当前活跃请求数")


# ─── Error 模型 ────────────────────────────────────────────


class ErrorResponse(BaseModel):
    """通用错误响应"""

    error: str = Field(..., description="错误类型或简要描述")
    detail: Optional[str] = Field(None, description="详细错误信息")
    code: Optional[int] = Field(None, description="业务错误码")
