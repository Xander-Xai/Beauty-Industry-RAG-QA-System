"""
核心管线模块 - 在线 RAG 推理管线上下文与异常定义
"""

from core.exceptions import (
    AdmissionRejectError,
    CacheError,
    EvidenceGateError,
    FallbackTriggered,
    GenerationError,
    PipelineError,
    RerankError,
    RetrievalError,
    RewriteError,
)
from core.pipeline import OnlineRAGPipeline
from core.pipeline_context import RequestContext, SessionState
