"""
BERT 复杂度评估器（readme 4.3 节）

模型：BERT 0.3B，二分类。准确率 97.2% / P99 ≤ 12ms 为设计目标，生产环境当前使用规则兜底。
功能：判断查询复杂度 → 简单问题路由到 vLLM-Gen-4B，复杂问题路由到 Qwen3-14B (4-bit NF4, PEFT-ready)

模型不存在时自动降级为增强规则评估
"""

from __future__ import annotations

import logging
import os

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)


class ComplexityEvaluator:
    """
    BERT 复杂度评估器

    二分类模型：
    - 0: 简单查询 → 路由到 vLLM-Gen-4B
    - 1: 复杂查询 → 路由到 Qwen3-14B (4-bit NF4, PEFT-ready)

    复杂查询特征：
    - 法规条文引用、多条件合规判断
    - 需要多文档交叉推理
    - 长上下文依赖
    """

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._use_model = False
        try:
            self.device = "cuda:1" if __import__("torch").cuda.is_available() else "cpu"
        except ImportError:
            self.device = "cpu"

        # 非生产模式直接使用规则，跳过模型加载（延迟优化：节省 ~17s）
        from common.config import is_production_mode
        if is_production_mode():
            self._try_load_model()
        else:
            logger.info("非生产模式，复杂度评估使用规则兜底（跳过 BERT 模型加载）")

    def _try_load_model(self):
        """尝试加载 BERT 复杂度分类模型，失败则使用规则兜底"""
        try:
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
        规则兜底评估（模型未加载时使用）

        复杂度关键词匹配（化妆品领域）
        """
        complex_keywords = [
            # 法规类
            "法规", "合规", "标准", "备案", "许可", "禁用", "安全评估", "毒理",
            "功效评价", "原料安全", "禁限用", "化妆品安全技术规范",
            # 研发类
            "配方", "复配", "工艺", "稳定性", "相容性", "防腐体系", "功效宣称",
            # 成分交叉
            "多成分", "交叉", "综合", "对比", "分析", "评估",
            # 条款引用
            "依据", "引用", "条款", "第", "条", "号",
            # 专业术语
            "INCI", "分子量", "浓度阈值", "PH范围", "使用量",
        ]
        score = sum(1 for kw in complex_keywords if kw in query)
        return score >= 2
