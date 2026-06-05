"""
Generation Service -- FastAPI 微服务

提供：
- 文本生成（含续写）
- KV Cache 准入控制
- 查询复杂度评估
"""

from __future__ import annotations

import logging
import os
import sys
import time
import threading
from typing import Any, Dict, Optional

# Ensure project root is on sys.path for common.* imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from llm_client import LLMClient
from kv_admission import KVAdmissionControl
from complexity_evaluator import ComplexityEvaluator
from common.models import (
    EvidenceGateResult,
    GenerationResult,
    QueryRewriteResult,
    RerankResult,
)
from common.service_auth import verify_service_token

logger = logging.getLogger(__name__)

app = FastAPI(title="Generation Service", version="1.0.0")

# -- singleton instances ---------------------------------------------------
llm_client = LLMClient()
kv_admission = KVAdmissionControl()
complexity_evaluator = ComplexityEvaluator()

# -- request / response schemas --------------------------------------------


class GenerateRequest(BaseModel):
    """POST /api/generate input."""

    ctx: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Request context dict containing: user_input, rewrite_result, "
            "rerank_results, evidence_result, session_id, max_output_tokens, "
            "target_model"
        ),
    )


class ContinuationRequest(BaseModel):
    """POST /api/continuation input."""

    ctx: Dict[str, Any] = Field(default_factory=dict)
    already_generated: str = ""


class AdmissionCheckRequest(BaseModel):
    """POST /api/admission-check input."""

    request_id: str
    input_tokens: int
    output_tokens: int
    business_type: str = "general"


class AdmissionReleaseRequest(BaseModel):
    """POST /api/admission-release input."""

    request_id: str


class ComplexityRequest(BaseModel):
    """POST /api/complexity input."""

    query: str


class AdmissionCheckResponse(BaseModel):
    admitted: bool
    reason: str
    kv_pressure: float


class ComplexityResponse(BaseModel):
    is_complex: bool


# -- helpers ----------------------------------------------------------------


def _rebuild_context(raw: Dict[str, Any]):
    """
    Reconstruct domain objects from the raw ctx dict.

    The pipeline passes serialised dicts across service boundaries; this
    helper hydrates them back into the Pydantic models that LLMClient
    expects.
    """

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.user_input = raw.get("user_input", "")
    ctx.session_id = raw.get("session_id")
    ctx.max_output_tokens = raw.get("max_output_tokens", 512)
    ctx.target_model = raw.get("target_model", "qwen3-4b")

    # rewrite_result
    wr = raw.get("rewrite_result")
    if isinstance(wr, dict):
        ctx.rewrite_result = QueryRewriteResult(**wr)
    elif wr is None:
        ctx.rewrite_result = QueryRewriteResult()
    else:
        ctx.rewrite_result = wr

    # rerank_results
    rr = raw.get("rerank_results", [])
    ctx.rerank_results = []
    for item in rr:
        if isinstance(item, dict):
            ctx.rerank_results.append(RerankResult(**item))
        else:
            ctx.rerank_results.append(item)

    # evidence_result
    er = raw.get("evidence_result")
    if isinstance(er, dict):
        ctx.evidence_result = EvidenceGateResult(**er)
    elif er is None:
        ctx.evidence_result = None
    else:
        ctx.evidence_result = er

    return ctx


# -- endpoints --------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "service": "generation-service"}


@app.get("/status")
async def status():
    kv_status = kv_admission.get_status()
    return {
        "service": "generation-service",
        "kv_pressure": kv_status["kv_pressure"],
        "active_requests": kv_status["active"],
        "kv_budget_gb": kv_status.get("kv_budget_gb", 0),
        "endpoints_alive": llm_client.router.check_any_endpoint_alive()
        if llm_client._router is not None
        else False,
    }


@app.post("/api/generate")
async def generate(req: GenerateRequest, _auth: None = Depends(verify_service_token)):
    """
    Main generation endpoint.

    Input ctx dict keys:
        user_input, rewrite_result, rerank_results, evidence_result,
        session_id, max_output_tokens, target_model
    """
    try:
        ctx = _rebuild_context(req.ctx)
        result = llm_client.generate(
            ctx,
            target_model=ctx.target_model,
            max_tokens=ctx.max_output_tokens,
        )
        return result.model_dump()
    except Exception as e:
        logger.error(f"Generation failed: {e}")
        raise HTTPException(status_code=500, detail="生成失败，请稍后重试")


@app.post("/api/continuation")
async def continuation(req: ContinuationRequest, _auth: None = Depends(verify_service_token)):
    """
    PRD §4.6 续写端点 — 上下文重建 + 约束式完整生成.

    关键修正：正确加载 SessionState（含锁定的 evidence doc_ids），
    避免因 session_state=None 导致证据锁定丢失。
    """
    try:
        ctx = _rebuild_context(req.ctx)

        # PRD §4.6: 从 SessionState 注册表加载会话状态（含锁定的 evidence doc_ids）
        session_state = None
        if ctx.session_id:
            from core.pipeline_context import SessionState
            session_state = SessionState.get_or_create(ctx.session_id)
            # 恢复锁定的证据文档 ID
            if session_state.locked_doc_ids:
                ctx.evidence_locked_doc_ids = list(session_state.locked_doc_ids)

        result = llm_client.generate_continuation(
            ctx,
            session_state=session_state,
            already_generated=req.already_generated,
            target_model=ctx.target_model,
            max_tokens=ctx.max_output_tokens,
        )
        return result.model_dump()
    except Exception as e:
        logger.error(f"Continuation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admission-check", response_model=AdmissionCheckResponse)
async def admission_check(req: AdmissionCheckRequest, _auth: None = Depends(verify_service_token)):
    """KV admission check."""
    admitted, reason, priority = kv_admission.admit(
        req.request_id, req.input_tokens, req.output_tokens, req.business_type
    )
    pressure = kv_admission.get_pressure()
    return AdmissionCheckResponse(
        admitted=admitted,
        reason=reason,
        kv_pressure=round(pressure, 4),
    )


@app.post("/api/admission-release")
async def admission_release(req: AdmissionReleaseRequest, _auth: None = Depends(verify_service_token)):
    """Release KV admission for a request."""
    kv_admission.release(req.request_id)
    return {"status": "released", "request_id": req.request_id}


@app.post("/api/complexity", response_model=ComplexityResponse)
async def complexity(req: ComplexityRequest, _auth: None = Depends(verify_service_token)):
    """Evaluate query complexity."""
    is_complex = complexity_evaluator.evaluate(req.query)
    return ComplexityResponse(is_complex=is_complex)


# -- main -------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8100)
