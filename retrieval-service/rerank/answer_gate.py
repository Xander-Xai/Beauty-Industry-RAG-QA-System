"""
Answer Gate (service version)

Migrated from retrieval/answer_gate.py.
Logic is identical; imports updated to use common.models.

NLI model verifies entailment relationship between Answer and Top-1 Doc.
- contradiction > 0.5 -> warning flag
- regulation contradiction -> forced rejection

GPU batch processing, latency <= 25ms
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional, Tuple

import numpy as np

# Ensure project root on sys.path for config.json and shared modules
PROJECT_ROOT = "/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class AnswerGate:
    """
    Answer Gate - NLI answer verification.

    Verifies logical consistency between the generated answer and the
    retrieved evidence, preventing LLM from generating content that
    contradicts the retrieval results.
    """

    def __init__(self):
        self.nli_threshold = config["generation"]["answer_gate"]["nli_threshold"]
        self._nli_model = None
        self._nli_tokenizer = None
        self._use_nli = False
        self._try_load_nli()
        logger.info("AnswerGate initialised")

    def _try_load_nli(self):
        """Attempt to load NLI model (cross-encoder nli); fall back to text similarity."""
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            # NLI model config: cross-encoder/nli-deberta or similar
            # Labels: 0=contradiction, 1=entailment, 2=neutral
            nli_model_path = config["gpu1"]["models"]["nli_model"]["model_path"]

            if not os.path.exists(nli_model_path):
                logger.info(f"NLI model not found ({nli_model_path}), falling back to text similarity")
                return

            self._nli_tokenizer = AutoTokenizer.from_pretrained(nli_model_path)
            self._nli_model = AutoModelForSequenceClassification.from_pretrained(nli_model_path)
            self._nli_model.to(self.device)
            self._nli_model.eval()
            self._use_nli = True
            logger.info(f"NLI model loaded: {nli_model_path}")
        except Exception as e:
            logger.info(f"NLI model load failed, falling back to text similarity: {e}")

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
        Verify answer-evidence consistency.

        Args:
            answer: LLM-generated answer
            top_doc: top-ranked retrieval document (RerankResult)
            is_regulation: whether this is a regulation query

        Returns:
            AnswerGateResult
        """
        from common.models import AnswerGateResult

        if top_doc is None:
            return AnswerGateResult(
                nli_contradiction_score=0.0,
                nli_entailment_score=0.0,
                passed=True,
                warning=False,
                is_regulation=is_regulation,
            )

        # NLI inference
        contradiction_score, entailment_score = self._nli_inference(
            top_doc.content, answer
        )

        # Decision logic
        warning = contradiction_score > 0.5
        passed = True

        if is_regulation and contradiction_score > 0.5:
            # Regulation: forced rejection
            passed = False
            logger.warning(
                f"Answer Gate: regulation answer contradicts evidence "
                f"(contradiction={contradiction_score:.3f}), forced rejection"
            )
        elif contradiction_score > self.nli_threshold:
            passed = False
            logger.warning(
                f"Answer Gate: answer contradicts evidence "
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
        Batch NLI verification (used by Evidence Gate for Top-3 doc consistency).

        Args:
            answer: LLM-generated answer
            top_docs: candidate document list (RerankResult)
            is_regulation: whether this is a regulation query

        Returns:
            [{"doc_id": str, "contradiction": float, "entailment": float}]
        """
        if not top_docs:
            return []

        # Batch-construct NLI pairs
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
        NLI inference.

        Returns:
            (contradiction_score, entailment_score)
        """
        if self._use_nli:
            return self._nli_inference_with_model(premise, hypothesis)
        else:
            return self._nli_inference_with_similarity(premise, hypothesis)

    def _batch_nli_inference(self, pairs: list) -> list[Tuple[float, float]]:
        """
        Batch NLI inference (shared model context).

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
        """NLI inference with model."""
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
        """Batch NLI inference with model."""
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
        Text similarity fallback (when NLI model is unavailable).

        Uses simple keyword overlap + cosine similarity to estimate NLI scores.
        """
        # Simple token overlap
        premise_tokens = set(premise)
        hypothesis_tokens = set(hypothesis)

        if not premise_tokens or not hypothesis_tokens:
            return 0.1, 0.3

        # Jaccard similarity as entailment estimate
        intersection = premise_tokens & hypothesis_tokens
        union = premise_tokens | hypothesis_tokens
        similarity = len(intersection) / len(union) if union else 0

        # Estimate entailment and contradiction scores
        entailment_score = min(similarity * 1.2, 0.9)
        # If hypothesis has much info not in premise, contradiction may be higher
        extra_ratio = len(hypothesis_tokens - premise_tokens) / len(premise_tokens) if premise_tokens else 0
        contradiction_score = max(0.0, min(extra_ratio * 0.3, 0.6))

        return contradiction_score, entailment_score
