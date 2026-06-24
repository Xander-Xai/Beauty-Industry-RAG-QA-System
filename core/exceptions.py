"""
自定义异常 - 管线各阶段错误与降级标记

异常层级：
- PipelineError: 管线顶层异常
  - RewriteError: Query Rewrite 失败（降级为规则兜底，不返回 503）
  - RetrievalError: 检索阶段异常
  - RerankError: 重排阶段异常
  - EvidenceGateError: Evidence Gate 异常
  - GenerationError: LLM 生成异常
  - AdmissionRejectError: KV 准入控制拒绝
  - CacheError: 缓存操作异常
  - FallbackTriggered: 降级触发标记（非异常，用于信号传递）
"""


class PipelineError(Exception):
    """管线顶层异常"""
    def __init__(self, message: str, stage: str = "unknown", recoverable: bool = True):
        super().__init__(message)
        self.stage = stage
        self.recoverable = recoverable


class RewriteError(PipelineError):
    """Query Rewrite 失败 - 降级为规则兜底，不返回 503"""
    def __init__(self, message: str = "Rewrite failed", fallback_fields: dict = None):
        super().__init__(message, stage="rewrite", recoverable=True)
        self.fallback_fields = fallback_fields or {
            "business_type": "general",
            "intent": "general",
            "requires_context": True,
            "confidence": 0.3,
            "fallback": True,
        }


class RetrievalError(PipelineError):
    """检索阶段异常"""
    def __init__(self, message: str = "Retrieval failed", path: str = ""):
        super().__init__(message, stage="retrieval", recoverable=True)
        self.path = path  # dense_bge / bm25_es / clip_visual / rewrite_variant


class RerankError(PipelineError):
    """重排阶段异常"""
    def __init__(self, message: str = "Rerank failed"):
        super().__init__(message, stage="rerank", recoverable=True)


class EvidenceGateError(PipelineError):
    """Evidence Ensemble Gate 异常"""
    def __init__(self, message: str = "Evidence gate evaluation failed"):
        super().__init__(message, stage="evidence_gate", recoverable=True)


class GenerationError(PipelineError):
    """LLM 生成异常"""
    def __init__(self, message: str = "Generation failed", model: str = ""):
        super().__init__(message, stage="generation", recoverable=True)
        self.model = model


class AdmissionRejectError(PipelineError):
    """KV 准入控制拒绝 - 按优先级降级"""
    def __init__(self, message: str = "Admission rejected", reason: str = "", priority: str = "P2"):
        super().__init__(message, stage="admission_control", recoverable=True)
        self.reason = reason
        self.priority = priority


class CacheError(PipelineError):
    """缓存操作异常 - 降级为直接执行"""
    def __init__(self, message: str = "Cache operation failed"):
        super().__init__(message, stage="cache", recoverable=True)


class InfrastructureError(PipelineError):
    """
    基础设施故障 — HTTP 503

    PRD §8: 系统仅在基础设施故障（Redis 连接断开、Qdrant 超时、
    ES 不可用）或极端过载时返回 HTTP 503。
    业务逻辑（Rewrite 失败、证据不足）一律返回 HTTP 200。
    """
    def __init__(self, message: str = "Infrastructure failure", service: str = ""):
        super().__init__(message, stage="infrastructure", recoverable=False)
        self.service = service  # redis / qdrant / elasticsearch / vllm


class FallbackTriggered(PipelineError):
    """
    降级触发标记（非真正异常）

    用于在管线中传递降级信号，例如：
    - Rewrite 降级为规则解析
    - CLIP 超时降级为纯文本检索
    - KV 压力过高降级模型
    """
    def __init__(self, message: str, fallback_type: str, original_stage: str = ""):
        super().__init__(message, stage=original_stage, recoverable=True)
        self.fallback_type = fallback_type
        self.original_stage = original_stage
