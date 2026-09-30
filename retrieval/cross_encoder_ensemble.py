"""
CrossEncoder Ensemble 重排模块（readme 7.3 Stage 2）

双模型轻量 Ensemble：
- CE-A：法律/成分调优版 CrossEncoder
- CE-B：通用语义 CrossEncoder
计算逻辑：CE_score = avg(CE_A(doc), CE_B(doc))
Platt Scaling：将 CE 原始分映射为校准概率 [0, 1]

GPU 批处理架构：
- Rerank Batch Aggregator 位于 GPU1
- time-based batching: 10-20ms 窗口
- size-based batching: max 64 pairs per batch
- 单 pair 等效延迟由 CPU 200-400ms 降至 GPU 1-3ms
"""

from __future__ import annotations

import logging
import math

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)


class PlattScaler:
    """
    Platt Scaling 校准器（PRD §7.3）

    将 CrossEncoder 原始分映射为校准概率。
    使用 sigmoid(A * x + B) 公式，参数通过少量校准数据拟合。

    冷启动：使用默认参数 A=-1.0, B=0.0（近似 S 形映射），
    后续由离线反馈闭环用标注数据更新 A, B。
    """

    def __init__(self):
        self._calibrators: dict[str, dict] = {}  # model_name -> {a, b}
        self._load_calibrators()

    def _load_calibrators(self):
        """从配置或校准数据加载 Platt Scaling 参数"""
        calib_config = config.get("retrieval", {}).get("cross_encoder", {}).get("platt_scaling", {})
        for model_name in ("ce_a", "ce_b", "ensemble"):
            params = calib_config.get(model_name, {})
            self._calibrators[model_name] = {
                "a": params.get("a", -1.0),
                "b": params.get("b", 0.0),
            }
        logger.info(f"PlattScaler 初始化: {self._calibrators}")

    def calibrate(self, raw_score: float, model_name: str = "ensemble") -> float:
        """
        将原始 CrossEncoder 分数映射为校准概率。

        Platt Scaling: P(y=1|s) = sigmoid(a * s + b)
        """
        params = self._calibrators.get(model_name, self._calibrators.get("ensemble"))
        a, b = params["a"], params["b"]
        # sigmoid(a * score + b)
        z = a * raw_score + b
        z = max(-500, min(500, z))  # 溢出保护
        return 1.0 / (1.0 + math.exp(-z))

    def calibrate_batch(self, raw_scores: list[float], model_name: str = "ensemble") -> list[float]:
        """批量校准"""
        return [self.calibrate(s, model_name) for s in raw_scores]

    def update_params(self, model_name: str, a: float, b: float):
        """更新校准参数（由离线反馈闭环调用）"""
        self._calibrators[model_name] = {"a": a, "b": b}
        logger.info(f"PlattScaler 参数更新: {model_name} -> a={a}, b={b}")


class CrossEncoderEnsemble:
    """
    CrossEncoder 集成重排器

    双模型 Ensemble + GPU 微批聚合
    输出 Top-K 文档及其经 Platt Scaling 校准的概率分数
    """

    def __init__(self):
        self._ce_a = None  # CrossEncoder-A (法律/成分)
        self._ce_b = None  # CrossEncoder-B (通用)
        self._batch_aggregator = None
        self._platt_scaler = PlattScaler()
        logger.info("CrossEncoderEnsemble 初始化完成")

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """
        PRD §7.3: Token 级截断，确保 CrossEncoder 输入不超过 max_tokens tokens。

        中文文本 512 字符可能对应 >512 tokens，必须使用 tokenizer 截断。
        截断策略：使用 CE-A 的 tokenizer 进行 encode → truncate → decode。
        若 tokenizer 不可用则回退到字符截断（保守估计中文 ~1.5 token/char）。
        """
        if not text:
            return ""
        try:
            tokenizer = self.ce_a.tokenizer
            encoded = tokenizer(
                text, truncation=True, max_length=max_tokens, return_tensors="pt", add_special_tokens=True
            )
            return tokenizer.decode(encoded["input_ids"][0], skip_special_tokens=True)
        except Exception:
            # 回退：中文保守估计 1.5 token/char，使用更短的字符截断
            fallback_len = max(max_tokens * 2 // 3, 128)
            return text[:fallback_len]

    @property
    def ce_a(self):
        if self._ce_a is None:
            from sentence_transformers import CrossEncoder

            model_path = config["gpu1"]["models"]["cross_encoder_a"]["model_path"]
            self._ce_a = CrossEncoder(model_path)
            logger.info(f"CrossEncoder-A 加载完成: {model_path}")
        return self._ce_a

    @property
    def ce_b(self):
        if self._ce_b is None:
            from sentence_transformers import CrossEncoder

            model_path = config["gpu1"]["models"]["cross_encoder_b"]["model_path"]
            self._ce_b = CrossEncoder(model_path)
            logger.info(f"CrossEncoder-B 加载完成: {model_path}")
        return self._ce_b

    @property
    def batch_aggregator(self):
        if self._batch_aggregator is None:
            from retrieval.rerank_batch_aggregator import RerankBatchAggregator

            self._batch_aggregator = RerankBatchAggregator()
        return self._batch_aggregator

    def rerank(
        self,
        query: str,
        candidates: list,
        top_k: int = 10,
    ) -> list:
        """
        CrossEncoder Ensemble 重排

        Args:
            query: 查询文本
            candidates: RerankResult 列表（BiEncoder 输出）
            top_k: 最终保留数量

        Returns:
            list[RerankResult] 按 ce_score_ensemble 降序排列
        """

        if not candidates:
            return []

        try:
            # PRD §7.3: 截断输入至 seq<=512 tokens（使用 tokenizer 精确截断，而非字符截断）
            max_seq_len = config.get("retrieval", {}).get("cross_encoder", {}).get("max_seq_length", 512)
            pairs = []
            for c in candidates:
                q_trunc = self._truncate_to_tokens(query, max_seq_len)
                d_trunc = self._truncate_to_tokens(c.content, max_seq_len)
                pairs.append((q_trunc, d_trunc))

            # 通过 Rerank Batch Aggregator 批处理执行
            scores_a = self.batch_aggregator.batch_predict(self.ce_a, pairs)
            scores_b = self.batch_aggregator.batch_predict(self.ce_b, pairs)

            # 计算 Ensemble 分数 + Platt Scaling 校准（PRD §7.3）
            raw_ensembles = []
            for i, candidate in enumerate(candidates):
                candidate.ce_score_a = float(scores_a[i]) if i < len(scores_a) else 0.0
                candidate.ce_score_b = float(scores_b[i]) if i < len(scores_b) else 0.0
                raw_avg = (candidate.ce_score_a + candidate.ce_score_b) / 2.0
                raw_ensembles.append(raw_avg)

            # Platt Scaling 校准：将原始分映射为 [0, 1] 概率
            calibrated_scores = self._platt_scaler.calibrate_batch(raw_ensembles, "ensemble")
            for i, candidate in enumerate(candidates):
                candidate.ce_score_ensemble = calibrated_scores[i]

            # 按校准后分数降序排序
            candidates.sort(key=lambda x: x.ce_score_ensemble, reverse=True)

            results = candidates[:top_k]

            # PRD §12.2 / GAP-20: 记录 CLIP 来源文档在 Rerank Top-K 中的贡献度
            clip_in_top = sum(1 for r in results if r.source and "clip" in r.source)
            clip_contribution = clip_in_top / len(results) if results else 0.0
            # 将贡献度附加到每条结果，供 pipeline 传递给 metrics_collector
            for r in results:
                r._clip_contribution_ratio = clip_contribution
            if clip_in_top > 0:
                logger.info(
                    f"CLIP 贡献度: {clip_in_top}/{len(results)} 条 Top-K 来自 CLIP 路径 (ratio={clip_contribution:.2f})"
                )

            logger.info(f"CrossEncoder Ensemble 重排完成: {len(candidates)} → {len(results)}")
            return results

        except Exception as e:
            logger.error(f"CrossEncoder Ensemble 重排失败: {e}")
            # 降级：保留 BiEncoder 排序
            return candidates[:top_k]
