"""
Rerank Batch Aggregator（readme 5.1 节）

位于 GPU1，接收来自检索阶段的候选文档流，
进行微批聚合（time-based 10-20ms 或 size-based 64 条请求）

批处理内容：
- CrossEncoder（40 pair → 合并为 batch matrix）
- NLI（Top-K 批量推理）
- BiEncoder（宽保留阶段批量编码）
- CLIP Text Encoder（同步请求批量处理）

并发隔离：各模型独立 CUDA context，Rerank 服务使用独立 CUDA Stream
"""

from __future__ import annotations

import json
import logging
import time
import threading
from typing import Callable, Optional
from collections import deque
from dataclasses import dataclass, field

import numpy as np

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


@dataclass
class BatchStats:
    """批处理统计"""
    total_batches: int = 0
    total_pairs: int = 0
    total_inference_time_ms: float = 0.0
    queue_delays_ms: list = field(default_factory=list)
    batch_sizes: list = field(default_factory=list)

    def record_batch(self, batch_size: int, queue_delay_ms: float, inference_ms: float):
        self.total_batches += 1
        self.total_pairs += batch_size
        self.total_inference_time_ms += inference_ms
        self.queue_delays_ms.append(queue_delay_ms)
        self.batch_sizes.append(batch_size)
        # 保持最近 1000 条
        if len(self.queue_delays_ms) > 1000:
            self.queue_delays_ms = self.queue_delays_ms[-1000:]
            self.batch_sizes = self.batch_sizes[-1000:]

    def get_fill_rate(self, max_batch: int) -> float:
        if not self.batch_sizes:
            return 0.0
        avg = sum(self.batch_sizes) / len(self.batch_sizes)
        return avg / max_batch if max_batch > 0 else 0.0

    def get_avg_delay_p50(self) -> float:
        if not self.queue_delays_ms:
            return 0.0
        sorted_d = sorted(self.queue_delays_ms)
        return sorted_d[len(sorted_d) // 2]

    def get_avg_delay_p99(self) -> float:
        if not self.queue_delays_ms:
            return 0.0
        sorted_d = sorted(self.queue_delays_ms)
        idx = int(len(sorted_d) * 0.99)
        return sorted_d[min(idx, len(sorted_d) - 1)]

    def get_avg_pairs_per_batch(self) -> float:
        return self.total_pairs / self.total_batches if self.total_batches > 0 else 0.0


class RerankBatchAggregator:
    """
    Rerank 微批聚合器

    聚合策略：
    - time-based batching: 10-20ms 窗口（跨请求聚合）
    - size-based batching: max 64 pairs per batch（动态填充）

    实现方式：
    当前为同步模式（直接批量推理），统计延迟信息
    生产环境可升级为异步模式（独立线程收集 pairs + 定时触发）
    """

    def __init__(self, config=None):
        if config is None:
            with open("config.json", encoding="utf-8") as f:
                config = json.load(f)
        self.time_window_ms = config["gpu1"]["rerank_batch_aggregator"]["time_window_ms"]
        self.max_batch_size = config["gpu1"]["rerank_batch_aggregator"]["max_batch_size"]
        self._lock = threading.Lock()
        self._stats = BatchStats()
        logger.info(
            f"RerankBatchAggregator 初始化: "
            f"time_window={self.time_window_ms}ms, max_batch={self.max_batch_size}"
        )

    def batch_predict(self, model, pairs: list[tuple[str, str]]) -> list[float]:
        """
        批量预测

        按 max_batch_size 分块执行，记录延迟统计。

        Args:
            model: CrossEncoder 或其他可 batch 预测的模型
            pairs: [(query, doc_text), ...] 列表

        Returns:
            list[float] 各 pair 的分数
        """
        if not pairs:
            return []

        t_start = time.time()

        try:
            # 按 max_batch_size 分块批量推理
            all_scores = []
            for i in range(0, len(pairs), self.max_batch_size):
                batch = pairs[i:i + self.max_batch_size]
                batch_scores = model.predict(
                    batch,
                    batch_size=min(len(batch), self.max_batch_size),
                )
                if hasattr(batch_scores, 'tolist'):
                    all_scores.extend(batch_scores.tolist())
                else:
                    all_scores.extend(list(batch_scores))

            inference_ms = (time.time() - t_start) * 1000

            with self._lock:
                self._stats.record_batch(
                    batch_size=len(pairs),
                    queue_delay_ms=0.0,  # 同步模式无排队延迟
                    inference_ms=inference_ms,
                )

            return all_scores

        except Exception as e:
            inference_ms = (time.time() - t_start) * 1000
            with self._lock:
                self._stats.record_batch(len(pairs), 0.0, inference_ms)
            # Re-raise GPU-related errors so batch_predict_with_fallback can handle them
            if isinstance(e, RuntimeError):
                raise
            logger.error(f"Batch predict 失败: {e}")
            return [0.0] * len(pairs)

    def batch_predict_nli(self, model, pairs: list[tuple[str, str]]) -> list:
        """
        NLI 批量推理（Evidence Gate + Answer Gate 共用）

        Args:
            model: NLI 分类模型
            pairs: [(premise, hypothesis), ...]

        Returns:
            list[tuple[float, float, float]] [(contradiction, entailment, neutral), ...]
        """
        if not pairs:
            return []

        t_start = time.time()
        try:
            all_results = []
            for i in range(0, len(pairs), self.max_batch_size):
                batch = pairs[i:i + self.max_batch_size]
                batch_results = model.predict(batch)
                if hasattr(batch_results, 'tolist'):
                    all_results.extend(batch_results.tolist())
                else:
                    all_results.extend(list(batch_results))

            inference_ms = (time.time() - t_start) * 1000
            with self._lock:
                self._stats.record_batch(len(pairs), 0.0, inference_ms)
            return all_results

        except Exception as e:
            logger.error(f"NLI batch predict 失败: {e}")
            return [()] * len(pairs)

    def batch_predict_with_fallback(self, model, pairs, use_cpu_fallback=False):
        """Batch predict with optional CPU fallback on GPU failure."""
        import torch
        try:
            return self.batch_predict(model, pairs)
        except (RuntimeError, torch.cuda.OutOfMemoryError) as e:
            if not use_cpu_fallback:
                raise
            logger.warning(f"GPU inference failed ({e}), falling back to CPU")
            return self._cpu_fallback_predict(model, pairs)

    def _cpu_fallback_predict(self, model, pairs):
        """CPU fallback: move model to CPU and predict in small batches."""
        import torch
        device_backup = None
        try:
            if hasattr(model, 'model') and hasattr(model.model, 'device'):
                device_backup = model.model.device
                model.model.cpu()
            batch_size = min(8, len(pairs))
            results = []
            for i in range(0, len(pairs), batch_size):
                batch = pairs[i:i + batch_size]
                scores = model.predict(batch)
                results.extend(scores.tolist() if hasattr(scores, 'tolist') else list(scores))
            return results
        finally:
            if device_backup is not None and hasattr(model, 'model'):
                model.model.to(device_backup)

    def get_stats(self) -> dict:
        """
        获取批处理统计信息（用于可观测性监控）

        返回：
        - batch_fill_rate: avg batch size / max batch size
        - queue_delay_p50/p99: 批处理排队延迟
        - total_batches: 总批次数
        - avg_pairs_per_batch: 平均每批 pair 数
        """
        with self._lock:
            return {
                "batch_fill_rate": round(self._stats.get_fill_rate(self.max_batch_size), 3),
                "queue_delay_p50": round(self._stats.get_avg_delay_p50(), 2),
                "queue_delay_p99": round(self._stats.get_avg_delay_p99(), 2),
                "total_batches": self._stats.total_batches,
                "total_pairs": self._stats.total_pairs,
                "avg_pairs_per_batch": round(self._stats.get_avg_pairs_per_batch(), 1),
                "avg_inference_ms": round(
                    self._stats.total_inference_time_ms / max(self._stats.total_batches, 1), 2
                ),
                "pending_count": 0,  # 同步模式无 pending
            }
