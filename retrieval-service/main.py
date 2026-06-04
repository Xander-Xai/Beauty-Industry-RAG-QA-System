"""
Retrieval Service - FastAPI application

Exposes the retrieval pipeline components as HTTP endpoints:
  - POST /api/recall          : parallel multi-path recall
  - POST /api/rerank           : BiEncoder + CrossEncoder reranking
  - POST /api/evidence-gate    : evidence evaluation
  - POST /api/answer-gate      : answer verification (NLI)
  - GET  /health               : liveness / readiness probe
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path and is the CWD so that all modules
# that load config.json at import time find it correctly.
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import numpy as np
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from common.models import (
    AnswerGateResult,
    EvidenceGateResult,
    RecallResult,
    RerankResult,
)
from common.service_auth import verify_service_token

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("retrieval-service")

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Retrieval Service",
    description="Multi-path recall, reranking, evidence gate and answer gate",
    version="2.0.0",
)

# ---------------------------------------------------------------------------
# Lazy-loaded singletons (loaded on first request to keep startup fast)
# ---------------------------------------------------------------------------
_recall_manager = None
_bi_encoder = None
_cross_encoder = None
_evidence_gate = None
_answer_gate = None
_embedding_service = None


def _get_recall_manager():
    global _recall_manager
    if _recall_manager is None:
        from retrieval_service.parallel_recall import ParallelRecallManager
        _recall_manager = ParallelRecallManager()
    return _recall_manager


def _get_bi_encoder():
    global _bi_encoder
    if _bi_encoder is None:
        from retrieval_service.rerank.bi_encoder import BiEncoderReranker
        _bi_encoder = BiEncoderReranker()
    return _bi_encoder


def _get_cross_encoder():
    global _cross_encoder
    if _cross_encoder is None:
        from retrieval_service.rerank.cross_encoder import CrossEncoderEnsemble
        _cross_encoder = CrossEncoderEnsemble()
    return _cross_encoder


def _get_evidence_gate():
    global _evidence_gate
    if _evidence_gate is None:
        from retrieval_service.rerank.evidence_gate import EvidenceEnsembleGate
        _evidence_gate = EvidenceEnsembleGate()
    return _evidence_gate


def _get_answer_gate():
    global _answer_gate
    if _answer_gate is None:
        from retrieval_service.rerank.answer_gate import AnswerGate
        _answer_gate = AnswerGate()
    return _answer_gate


def _get_embedding_service():
    global _embedding_service
    if _embedding_service is None:
        from models.embedding_service import EmbeddingService
        _embedding_service = EmbeddingService()
    return _embedding_service


# ═══════════════════════════════════════════════════════════════════════════
# Request / Response schemas (API boundary types)
# ═══════════════════════════════════════════════════════════════════════════


class RecallRequest(BaseModel):
    query: str
    query_embedding: Optional[List[float]] = None
    user_role_mask: int = 0
    user_dept_mask: int = 0
    use_clip: bool = True
    top_k_per_path: Dict[str, Any] = Field(default_factory=dict)


class RecallResponse(BaseModel):
    results: List[RecallResult]


class RerankRequest(BaseModel):
    query: str
    candidates: List[RecallResult]
    top_k: int = 10


class RerankResponse(BaseModel):
    results: List[RerankResult]


class EvidenceGateRequest(BaseModel):
    query: str
    rerank_results: List[RerankResult]
    retrieval_agreement_score: float = 0.0


class AnswerGateRequest(BaseModel):
    answer: str
    top_doc: Optional[RerankResult] = None
    is_regulation: bool = False


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "retrieval-service"
    timestamp: float = Field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/health", response_model=HealthResponse)
async def health():
    """Liveness / readiness probe."""
    return HealthResponse()


@app.post("/api/recall", response_model=RecallResponse)
async def api_recall(req: RecallRequest, _auth: None = Depends(verify_service_token)):
    """
    Parallel multi-path recall.

    Runs dense (BGE), BM25 (ES), CLIP (visual), and query-rewrite-variant
    paths concurrently and returns the union recall set.
    """
    t0 = time.time()
    try:
        # Compute query embedding if not provided by caller
        if req.query_embedding is not None:
            query_embedding = np.array(req.query_embedding, dtype=np.float32)
        else:
            embedding_svc = _get_embedding_service()
            query_embedding = embedding_svc.encode_text(req.query).flatten()

        manager = _get_recall_manager()

        results: List[RecallResult] = manager.execute(
            query=req.query,
            query_embedding=query_embedding,
            user_role_mask=req.user_role_mask,
            user_dept_mask=req.user_dept_mask,
            use_clip=req.use_clip,
            top_k_per_path=req.top_k_per_path or None,
        )

        elapsed_ms = (time.time() - t0) * 1000
        logger.info(
            f"POST /api/recall -> {len(results)} results in {elapsed_ms:.1f} ms"
        )
        return RecallResponse(results=results)

    except Exception as e:
        logger.error(f"/api/recall failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/rerank", response_model=RerankResponse)
async def api_rerank(req: RerankRequest, _auth: None = Depends(verify_service_token)):
    """
    BiEncoder wide-preservation rerank followed by CrossEncoder ensemble rerank.

    Stage 1: BiEncoder retains Top-150 candidates.
    Stage 2: CrossEncoder ensemble produces the final Top-K.
    """
    t0 = time.time()
    try:
        bi_encoder = _get_bi_encoder()
        cross_encoder = _get_cross_encoder()

        # Stage 1: BiEncoder wide-preservation
        bi_results: List[RerankResult] = bi_encoder.rerank(
            query=req.query,
            candidates=req.candidates,
            top_k=min(150, len(req.candidates)),
        )

        # Stage 2: CrossEncoder ensemble fine-ranking
        ce_results: List[RerankResult] = cross_encoder.rerank(
            query=req.query,
            candidates=bi_results,
            top_k=req.top_k,
        )

        elapsed_ms = (time.time() - t0) * 1000
        logger.info(
            f"POST /api/rerank -> {len(ce_results)} results in {elapsed_ms:.1f} ms"
        )
        return RerankResponse(results=ce_results)

    except Exception as e:
        logger.error(f"/api/rerank failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/evidence-gate", response_model=EvidenceGateResult)
async def api_evidence_gate(req: EvidenceGateRequest, _auth: None = Depends(verify_service_token)):
    """
    Evidence Ensemble Gate evaluation.

    Computes a multi-dimensional confidence score from CE Top-1/Top-3,
    retrieval agreement, and cross-document NLI consistency.
    """
    t0 = time.time()
    try:
        gate = _get_evidence_gate()

        result: EvidenceGateResult = gate.evaluate(
            query=req.query,
            rerank_results=req.rerank_results,
            retrieval_agreement_score=req.retrieval_agreement_score,
        )

        elapsed_ms = (time.time() - t0) * 1000
        logger.info(
            f"POST /api/evidence-gate -> decision={result.decision} "
            f"score={result.evidence_score:.3f} in {elapsed_ms:.1f} ms"
        )
        return result

    except Exception as e:
        logger.error(f"/api/evidence-gate failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/answer-gate", response_model=AnswerGateResult)
async def api_answer_gate(req: AnswerGateRequest, _auth: None = Depends(verify_service_token)):
    """
    Answer Gate: NLI-based verification of generated answer against top document.

    For regulation queries, contradiction > 0.5 triggers forced rejection.
    """
    t0 = time.time()
    try:
        gate = _get_answer_gate()

        result: AnswerGateResult = gate.verify(
            answer=req.answer,
            top_doc=req.top_doc,
            is_regulation=req.is_regulation,
        )

        elapsed_ms = (time.time() - t0) * 1000
        logger.info(
            f"POST /api/answer-gate -> passed={result.passed} "
            f"contradiction={result.nli_contradiction_score:.3f} "
            f"in {elapsed_ms:.1f} ms"
        )
        return result

    except Exception as e:
        logger.error(f"/api/answer-gate failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8200,
        reload=False,
        log_level="info",
    )
