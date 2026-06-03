"""
Answer Gate 模块（readme 8 节 Fail-safe 机制）

NLI 模型校验 Answer 与 Top1 Doc 的蕴含关系
- contradiction > 0.5 → 标记警告
- 法规类 contradiction → 强制拒答

GPU 批处理，延迟 ≤25ms
"""

from __future__ import annotations

import json
import logging
from typing import Optional, Tuple

import numpy as np

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class AnswerGate:
    """
    Answer Gate - NLI 答案校验

    校验生成答案与检索证据的逻辑一致性，
    防止 LLM 生成与检索证据矛盾的内容。
    """

    def __init__(self):
        self.nli_threshold = config["generation"]["answer_gate"]["nli_threshold"]
        self._nli_model = None
        self._nli_tokenizer = None
        self._use_nli = False
        self._try_load_nli()
        logger.info("AnswerGate 初始化完成")

    def _try_load_nli(self):
        """尝试加载 NLI 模型（cross-encoder nli），失败则使用文本相似度兜底"""
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            # NLI 模型配置：使用 cross-encoder/nli-deberta 或类似模型
            # 标签: 0=contradiction, 1=entailment, 2=neutral
            nli_model_path = config["gpu1"]["models"]["nli_model"]["model_path"]

            import os
            if not os.path.exists(nli_model_path):
                logger.info(f"NLI 模型不存在 ({nli_model_path})，使用文本相似度兜底")
                return

            self._nli_tokenizer = AutoTokenizer.from_pretrained(nli_model_path)
            self._nli_model = AutoModelForSequenceClassification.from_pretrained(nli_model_path)
            self._nli_model.to(self.device)
            self._nli_model.eval()
            self._use_nli = True
            logger.info(f"NLI 模型加载完成: {nli_model_path}")
        except Exception as e:
            logger.info(f"NLI 模型加载失败，使用文本相似度兜底: {e}")

    @property
    def device(self):
        import torch
        return "cuda:1" if torch.cuda.is_available() else "cpu"

    def verify(
        self,
        answer: str,
        top_doc=None,
        is_regulation: bool = False,
    ) -> "AnswerGateResult":
        """
        验证答案与证据的一致性

        Args:
            answer: LLM 生成的回答
            top_doc: 排名第一的检索文档（RerankResult）
            is_regulation: 是否为法规类查询

        Returns:
            AnswerGateResult
        """
        from core.pipeline_context import AnswerGateResult

        if top_doc is None:
            return AnswerGateResult(
                nli_contradiction_score=0.0,
                nli_entailment_score=0.0,
                passed=True,
                warning=False,
                is_regulation=is_regulation,
            )

        # NLI 推理
        contradiction_score, entailment_score = self._nli_inference(
            top_doc.content, answer
        )

        # 决策逻辑
        warning = contradiction_score > 0.5
        passed = True

        if is_regulation and contradiction_score > 0.5:
            # 法规类强制拒答
            passed = False
            logger.warning(
                f"Answer Gate: 法规类答案与证据矛盾 "
                f"(contradiction={contradiction_score:.3f})，强制拒答"
            )
        elif contradiction_score > self.nli_threshold:
            passed = False
            logger.warning(
                f"Answer Gate: 答案与证据矛盾 "
                f"(contradiction={contradiction_score:.3f} > {self.nli_threshold})"
            )

        return AnswerGateResult(
            nli_contradiction_score=contradiction_score,
            nli_entailment_score=entailment_score,
            passed=passed,
            warning=warning,
            is_regulation=is_regulation,
        )

    def batch_verify(
        self,
        answer: str,
        top_docs: list,
        is_regulation: bool = False,
    ) -> list:
        """
        批量 NLI 校验（用于 Evidence Gate 的 Top-3 文档间一致性检测）

        Args:
            answer: LLM 生成的回答
            top_docs: 候选文档列表（RerankResult）
            is_regulation: 是否为法规类查询

        Returns:
            [{"doc_id": str, "contradiction": float, "entailment": float}]
        """
        if not top_docs:
            return []

        # 批量构造 NLI pairs
        pairs = [(doc.content, answer) for doc in top_docs]
        results = self._batch_nli_inference(pairs)

        return [
            {
                "doc_id": doc.doc_id,
                "contradiction": results[i][0],
                "entailment": results[i][1],
            }
            for i, doc in enumerate(top_docs)
        ]

    def _nli_inference(self, premise: str, hypothesis: str) -> Tuple[float, float]:
        """
        NLI 推理

        Returns:
            (contradiction_score, entailment_score)
        """
        if self._use_nli:
            return self._nli_inference_with_model(premise, hypothesis)
        else:
            return self._nli_inference_with_similarity(premise, hypothesis)

    def _batch_nli_inference(self, pairs: list) -> list[Tuple[float, float]]:
        """
        批量 NLI 推理（共享模型上下文）

        Args:
            pairs: [(premise, hypothesis), ...]

        Returns:
            [(contradiction_score, entailment_score), ...]
        """
        if self._use_nli:
            return self._batch_nli_with_model(pairs)
        else:
            return [self._nli_inference_with_similarity(p, h) for p, h in pairs]

    def _nli_inference_with_model(self, premise: str, hypothesis: str) -> Tuple[float, float]:
        """使用 NLI 模型推理"""
        import torch

        inputs = self._nli_tokenizer(
            premise, hypothesis,
            return_tensors="pt", truncation=True, max_length=512,
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self._nli_model(**inputs)
            logits = outputs.logits
            probs = torch.softmax(logits, dim=-1)[0]

        # DeBERTa NLI: 0=contradiction, 1=entailment, 2=neutral
        contradiction_score = probs[0].item()
        entailment_score = probs[1].item()

        return contradiction_score, entailment_score

    def _batch_nli_with_model(self, pairs: list) -> list[Tuple[float, float]]:
        """批量 NLI 推理"""
        import torch

        premises = [p[0] for p in pairs]
        hypotheses = [p[1] for p in pairs]

        inputs = self._nli_tokenizer(
            premises, hypotheses,
            return_tensors="pt", truncation=True, max_length=512,
            padding=True,
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self._nli_model(**inputs)
            logits = outputs.logits
            probs = torch.softmax(logits, dim=-1)

        results = []
        for i in range(len(pairs)):
            contradiction = probs[i][0].item()
            entailment = probs[i][1].item()
            results.append((contradiction, entailment))

        return results

    def _nli_inference_with_similarity(self, premise: str, hypothesis: str) -> Tuple[float, float]:
        """
        文本相似度兜底（NLI 模型不可用时）

        使用简单的关键词重叠 + 余弦相似度估计 NLI 分数
        """
        # 简单的关键词重叠度
        premise_tokens = set(premise)
        hypothesis_tokens = set(hypothesis)

        if not premise_tokens or not hypothesis_tokens:
            return 0.1, 0.3

        # Jaccard 相似度作为蕴含估计
        intersection = premise_tokens & hypothesis_tokens
        union = premise_tokens | hypothesis_tokens
        similarity = len(intersection) / len(union) if union else 0

        # 估计蕴含和矛盾分数
        entailment_score = min(similarity * 1.2, 0.9)
        # 如果假设中有很多前提中没有的信息，矛盾可能更高
        extra_ratio = len(hypothesis_tokens - premise_tokens) / len(premise_tokens) if premise_tokens else 0
        contradiction_score = max(0.0, min(extra_ratio * 0.3, 0.6))

        return contradiction_score, entailment_score
