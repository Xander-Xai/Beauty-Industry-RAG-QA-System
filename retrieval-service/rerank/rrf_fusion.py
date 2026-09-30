"""
RRF (Reciprocal Rank Fusion) — 多路召回结果融合

将多路召回结果按 Reciprocal Rank Fusion 算法加权融合，
替代简单的列表拼接。支持每路独立权重，通过 config.json 配置。

算法：
  score(doc) = Σ_weight(path) × 1 / (k + rank(path, doc))

Reference: Cormack et al. (2009), "Reciprocal rank fusion outperforms
condorcet and individual rank learning methods"
"""

from __future__ import annotations

import logging
from collections import defaultdict

from common.models import RecallResult

logger = logging.getLogger(__name__)


def rrf_fusion(
    results_map: dict[str, list[RecallResult]],
    k: int = 60,
    weights: dict[str, float] | None = None,
) -> list[RecallResult]:
    """
    Reciprocal Rank Fusion：多路召回结果融合。

    Args:
        results_map: 各路召回结果，key 为路径名（dense_bge, bm25_es 等）
        k: RRF 常数（防止低排名结果得分过高），默认 60
        weights: 各路权重，None 或空 dict 时等权重（1.0）

    Returns:
        按 RRF score 降序排列的 RecallResult 列表（已去元重复）
    """
    if not results_map:
        return []

    if weights is None:
        weights = {}

    doc_scores: dict[str, float] = defaultdict(float)
    doc_paths: dict[str, int] = {}
    doc_source: dict[str, str] = {}
    doc_content: dict[str, str] = {}
    doc_metadata: dict[str, dict] = {}

    for path_name, results in results_map.items():
        path_weight = weights.get(path_name, 1.0)
        if not results:
            continue

        for rank, doc in enumerate(results):
            rrf_score = path_weight / (k + (rank + 1))
            doc_scores[doc.doc_id] += rrf_score
            doc_paths[doc.doc_id] = doc_paths.get(doc.doc_id, 0) + 1
            if doc.doc_id not in doc_source:
                doc_source[doc.doc_id] = doc.source
                doc_content[doc.doc_id] = doc.content
                doc_metadata[doc.doc_id] = doc.metadata

    sorted_docs = sorted(
        doc_scores.items(),
        key=lambda x: (x[1], doc_paths.get(x[0], 0)),
        reverse=True,
    )

    fused = []
    for doc_id, score in sorted_docs:
        fused.append(
            RecallResult(
                doc_id=doc_id,
                content=doc_content.get(doc_id, ""),
                score=score,
                source=doc_source.get(doc_id, "rrf_fused"),
                metadata=doc_metadata.get(doc_id, {}),
            )
        )

    input_count = sum(len(v) for v in results_map.values())
    logger.info(f"RRF fusion complete: {input_count} inputs -> {len(fused)} unique docs")
    return fused
