"""
BERT 复杂度评估器（readme 4.3 节）

模型：BERT 0.3B，二分类，准确率 97.2%，P99 ≤ 12ms
功能：判断查询复杂度 → 简单问题路由到 vLLM-Gen-4B，复杂问题路由到 Qwen3-14B+QLoRA

模型不存在时自动降级为增强规则评估
"""

from __future__ import annotations

import json
import logging
import os

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class ComplexityEvaluator:
    """
    BERT 复杂度评估器

    二分类模型：
    - 0: 简单查询 → 路由到 vLLM-Gen-4B
    - 1: 复杂查询 → 路由到 Qwen3-14B+QLoRA

    复杂查询特征：
    - 法规条文引用、多条件合规判断
    - 需要多文档交叉推理
    - 长上下文依赖
    """

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._use_model = False
        self.device = "cuda:1" if __import__("torch").cuda.is_available() else "cpu"
        self._try_load_model()

    def _try_load_model(self):
        """尝试加载 BERT 复杂度分类模型，失败则使用规则兜底"""
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            model_path = config["gpu1"]["models"]["bert_complexity"]["model_path"]
            if not os.path.exists(model_path):
                logger.info(f"BERT 复杂度模型不存在 ({model_path})，使用规则兜底")
                return

            self._tokenizer = AutoTokenizer.from_pretrained(model_path)
            self._model = AutoModelForSequenceClassification.from_pretrained(model_path)
            self._model.to(self.device)
            self._model.eval()
            self._use_model = True
            logger.info(f"BERT 复杂度模型加载完成: {model_path}")
        except Exception as e:
            logger.info(f"BERT 复杂度模型加载失败，使用规则兜底: {e}")

    @property
    def model(self):
        return self._model if self._use_model else None

    def evaluate(self, query: str) -> bool:
        """
        评估查询复杂度

        Args:
            query: 用户查询文本

        Returns:
            True: 复杂查询（路由到 14B）
            False: 简单查询（路由到 4B）
        """
        if self._use_model and self.model is not None:
            return self._evaluate_with_model(query)
        else:
            return self._evaluate_with_rules(query)

    def _evaluate_with_model(self, query: str) -> bool:
        """使用 BERT 模型评估"""
        import torch
        inputs = self._tokenizer(
            query, return_tensors="pt",
            truncation=True, max_length=128,
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = self._model(**inputs)
        prediction = torch.argmax(outputs.logits, dim=1).item()
        confidence = torch.softmax(outputs.logits, dim=1).max().item()

        logger.debug(f"BERT 复杂度评估: prediction={prediction}, confidence={confidence:.3f}")
        return prediction == 1

    def _evaluate_with_rules(self, query: str) -> bool:
        """
        增强规则兜底评估（模型未加载时使用）

        采用多维度加权评分，提高准确率：
        - 关键词匹配（法规/研发/成分相关）
        - 查询长度
        - 句子结构复杂度
        - 实体密度
        """
        # 1. 高权重关键词（法规/合规/多条件判断）
        high_weight_keywords = [
            "法规", "合规", "标准", "备案", "许可",
            "安全评估", "毒理", "功效评价",
            "禁止", "限制", "不允许", "必须",
        ]
        # 2. 中权重关键词（研发/分析/对比）
        mid_weight_keywords = [
            "配方", "成分", "对比", "分析", "评估",
            "研发", "工艺", "制备", "INCI",
            "浓度", "比例", "含量",
        ]
        # 3. 低权重关键词（通用复杂信号）
        low_weight_keywords = [
            "依据", "引用", "条款", "综合",
            "审核", "交叉", "多成分",
        ]

        score = 0
        for kw in high_weight_keywords:
            if kw in query:
                score += 3
        for kw in mid_weight_keywords:
            if kw in query:
                score += 2
        for kw in low_weight_keywords:
            if kw in query:
                score += 1

        # 查询长度加权（长查询更可能是复杂查询）
        if len(query) > 50:
            score += 2
        elif len(query) > 30:
            score += 1

        # 多问号/多条件判断
        if query.count("？") > 1 or query.count("?") > 1:
            score += 1

        return score >= 3
