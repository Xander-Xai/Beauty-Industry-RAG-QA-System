"""
核心管线模块 - 在线 RAG 推理管线上下文与异常定义
"""
from core.pipeline_context import RequestContext, SessionState
from core.pipeline import OnlineRAGPipeline
from core.exceptions import (
    PipelineError,
    RewriteError,
    RetrievalError,
    RerankError,
    EvidenceGateError,
    GenerationError,
    AdmissionRejectError,
    CacheError,
    FallbackTriggered,
)
