"""
Pydantic models for inter-service communication.

Every microservice boundary crosses these types.  All fields use sensible
defaults so callers only need to supply what matters to them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ── Query Rewrite ────────────────────────────────────────────────────────


class QueryRewriteResult(BaseModel):
    """Output of the rewrite-service."""

    rewritten_query: str = ""
    business_type: str = "general"
    intent: str = ""
    requires_context: bool = False
    standardized_entities: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    fallback: bool = False


# ── Retrieval ────────────────────────────────────────────────────────────


class RecallResult(BaseModel):
    """Single document returned by any recall path (dense, BM25, CLIP, ...)."""

    doc_id: str
    content: str
    score: float
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RerankResult(BaseModel):
    """Single document after cross-encoder / bi-encoder re-ranking."""

    doc_id: str
    content: str
    source: str = ""
    ce_score_a: float = 0.0
    ce_score_b: float = 0.0
    ce_score_ensemble: float = 0.0
    bi_score: float = 0.0
    nli_score: float = 0.0
    final_score: float = 0.0


class EvidenceGateResult(BaseModel):
    """Decision output from the evidence-gate component."""

    evidence_score: float = 0.0
    ce_top1_score: float = 0.0
    ce_top3_mean_score: float = 0.0
    retrieval_agreement_score: float = 0.0
    doc_consistency_score: float = 0.0
    decision: str = "reject"  # "pass" | "enhanced_generate" | "reject"
    top_docs: list[RerankResult] = Field(default_factory=list)


# ── Generation ───────────────────────────────────────────────────────────


class GenerationResult(BaseModel):
    """Output of the generation-service."""

    answer: str = ""
    model_used: str = ""
    max_tokens: int = 0
    has_more: bool = False
    session_id: str | None = None
    answer_outline: list[str] = Field(default_factory=list)


class AnswerGateResult(BaseModel):
    """NLI-based answer quality gate output."""

    nli_contradiction_score: float = 0.0
    nli_entailment_score: float = 0.0
    passed: bool = True
    warning: bool = False
    is_regulation: bool = False


# ── Auth / Identity ─────────────────────────────────────────────────────


class UserIdentity(BaseModel):
    """Lightweight user identity carried through the request pipeline."""

    user_id: str = "anonymous"
    user_role_mask: int = 0
    user_dept_mask: int = 0


# ── Cache ────────────────────────────────────────────────────────────────


class CacheEntry(BaseModel):
    """A single cache entry (L2 / Redis)."""

    key: str
    value: dict[str, Any]
    role_mask: int = 0
    dept_mask: int = 0
    ttl: int = 3600


# ── Generic request / response envelopes ─────────────────────────────────


class ServiceRequest(BaseModel):
    """Generic envelope for any inter-service RPC request."""

    query: str = ""
    request_id: str = ""
    session_id: str | None = None
    user: UserIdentity = Field(default_factory=UserIdentity)
    context: dict[str, Any] = Field(default_factory=dict)


class ServiceResponse(BaseModel):
    """Generic envelope for any inter-service RPC response."""

    success: bool = True
    request_id: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    latency_ms: float = 0.0
