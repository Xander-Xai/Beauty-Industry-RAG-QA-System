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
from concurrent.futures import Future

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


@dataclass
class _PendingItem:
    """异步模式下待批处理的单个请求"""
    pairs: list
    future: Future
    submit_time: float


class RerankBatchAggregator:
    """
    Rerank 微批聚合器

    聚合策略：
    - time-based batching: 10-20ms 窗口（跨请求聚合）
    - size-based batching: max 64 pairs per batch（动态填充）

    异步模式（PRD §7.3）：
    - 独立后台线程收集各请求提交的 pairs
    - 在 time_window_ms 窗口到期或累积对数达到 max_batch_size 时触发推理
    - 跨请求共享 GPU 批处理，单 pair 等效延迟由 CPU 200-400ms 降至 1-3ms
    """

    def __init__(self, config=None):
        if config is None:
            with open("config.json", encoding="utf-8") as f:
                config = json.load(f)
        self.time_window_ms = config["gpu1"]["rerank_batch_aggregator"]["time_window_ms"]
        self.max_batch_size = config["gpu1"]["rerank_batch_aggregator"]["max_batch_size"]
        self._lock = threading.Lock()
        self._stats = BatchStats()

        # ── 异步跨请求批处理（PRD §7.3 time-based batching）──
        self._pending: list[_PendingItem] = []
        self._flush_event = threading.Event()
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._batch_worker_loop, daemon=True, name="rerank-batch-worker"
        )
        self._worker_thread.start()

        logger.info(
            f"RerankBatchAggregator 初始化: "
            f"time_window={self.time_window_ms}ms, max_batch={self.max_batch_size}, "
            f"mode=async"
        )

    # ── 异步 Worker 循环 ──────────────────────────────────

    def _batch_worker_loop(self):
        """
        后台线程：在时间窗口内收集跨请求 pairs，窗口到期后统一推理。

        触发条件（满足任一即 flush）：
        1. 累积 pair 数 >= max_batch_size
        2. 距最早 submit_time 已过 time_window_ms
        """
        while self._running:
            # 等待直到有 pending 或超时
            self._flush_event.wait(timeout=self.time_window_ms / 1000.0)
            self._flush_event.clear()

            self._flush_if_ready()

    def _flush_if_ready(self, force=False):
        """
        检查是否满足 flush 条件，满足则取出 pending pairs 并执行推理。

        Args:
            force: 强制 flush（窗口到期时调用）
        """
        while True:
            batch_items = []
            with self._lock:
                if not self._pending:
                    break

                now = time.time()
                oldest_time = self._pending[0].submit_time
                window_exceeded = (now - oldest_time) * 1000 >= self.time_window_ms

                if force or window_exceeded or len(self._pending) >= self.max_batch_size:
                    # 取出最多 max_batch_size 个 pair
                    remaining = self.max_batch_size
                    while self._pending and remaining > 0:
                        item = self._pending.pop(0)
                        batch_items.append(item)
                        remaining -= len(item.pairs)
                else:
                    break

            if not batch_items:
                break

            # 合并所有 pairs 并追踪映射关系
            all_pairs = []
            mapping = []  # (item_index, start_in_all, count)
            for item in batch_items:
                start = len(all_pairs)
                all_pairs.extend(item.pairs)
                mapping.append((item, start, len(item.pairs)))

            # 分块执行推理
            scores = self._execute_batch_sync(all_pairs)

            # 将结果分发回各 Future
            for item, start, count in mapping:
                item_scores = scores[start:start + count]
                item.future.set_result(item_scores)

    def _execute_batch_sync(self, pairs: list[tuple[str, str]]) -> list[float]:
        """同步执行一批 pairs 的推理（CrossEncoder 模型）"""
        t_start = time.time()
        queue_delay = 0.0
        if pairs:
            queue_delay = (time.time() - pairs[0][0].submit_time if hasattr(pairs, '_submit_time') else 0.0) * 1000

        all_scores = []
        for i in range(0, len(pairs), self.max_batch_size):
            batch = pairs[i:i + self.max_batch_size]
            try:
                from sentence_transformers import CrossEncoder
                # 使用缓存的模型实例（避免重复加载）
                if not hasattr(self, '_ce_model'):
                    model_path = config["gpu1"]["models"].get("cross_encoder_a", {}).get("model_path", "")
                    self._ce_model = CrossEncoder(model_path) if model_path else None
                if self._ce_model:
                    batch_scores = self._ce_model.predict(batch, batch_size=min(len(batch), self.max_batch_size))
                    if hasattr(batch_scores, 'tolist'):
                        all_scores.extend(batch_scores.tolist())
                    else:
                        all_scores.extend(list(batch_scores))
                else:
                    all_scores.extend([0.0] * len(batch))
            except Exception as e:
                logger.error(f"异步批推理失败: {e}")
                all_scores.extend([0.0] * len(batch))

        inference_ms = (time.time() - t_start) * 1000
        with self._lock:
            self._stats.record_batch(len(pairs), queue_delay, inference_ms)
        return all_scores

    # ── 公开接口 ───────────────────────────────────────────

    def submit_batch(self, pairs: list[tuple[str, str]], timeout_ms: float = None) -> Future:
        """
        异步提交 pairs 到批处理队列（跨请求聚合）。

        Args:
            pairs: [(query, doc_text), ...] 列表
            timeout_ms: 最大等待时间（默认 time_window_ms * 2）

        Returns:
            Future[list[float]] — 结果为各 pair 的分数列表
        """
        timeout_s = (timeout_ms or self.time_window_ms * 2) / 1000.0
        future = Future()
        item = _PendingItem(pairs=pairs, future=future, submit_time=time.time())

        with self._lock:
            self._pending.append(item)

        # 唤醒 worker 线程检查是否需要 flush
        self._flush_event.set()

        return future.result(timeout=timeout_s)

    def batch_predict(self, model, pairs: list[tuple[str, str]]) -> list[float]:
        """
        批量预测（兼容同步调用）

        优先使用异步跨请求聚合；若异步不可用则回退为同步模式。

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
        - pending_count: 异步队列中待处理 pair 数
        """
        with self._lock:
            pending_count = sum(len(item.pairs) for item in self._pending)
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
                "pending_count": pending_count,
            }
