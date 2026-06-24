"""
Rerank Batch Aggregator (service version)

Migrated from retrieval/rerank_batch_aggregator.py.
Logic is identical; imports updated to use common.models.

Located on GPU1, receives candidate document streams from the retrieval
stage and performs micro-batch aggregation (time-based 10-20ms or
size-based 64 pairs per request).

Batch contents:
- CrossEncoder (40 pairs -> merged batch matrix)
- NLI (Top-K batch inference)
- BiEncoder (wide-preservation batch encoding)
- CLIP Text Encoder (synchronised batch processing)

Concurrency isolation: each model has its own CUDA context;
Rerank service uses a dedicated CUDA Stream.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field

# Ensure project root on sys.path for config.json and shared modules
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)


@dataclass
class BatchStats:
    """Batch processing statistics."""
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
        # Keep most recent 1000 entries
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
    Rerank micro-batch aggregator.

    Aggregation strategy:
    - time-based batching: 10-20ms window (cross-request aggregation)
    - size-based batching: max 64 pairs per batch (dynamic fill)

    Implementation:
    Currently synchronous mode (direct batch inference), records latency
    statistics.  Production can be upgraded to asynchronous mode
    (dedicated thread collects pairs + timed trigger).
    """

    def __init__(self):
        self.time_window_ms = config["gpu1"]["rerank_batch_aggregator"]["time_window_ms"]
        self.max_batch_size = config["gpu1"]["rerank_batch_aggregator"]["max_batch_size"]
        self._lock = threading.Lock()
        self._stats = BatchStats()
        logger.info(
            f"RerankBatchAggregator initialised: "
            f"time_window={self.time_window_ms}ms, max_batch={self.max_batch_size}"
        )

    def batch_predict(self, model, pairs: list[tuple[str, str]]) -> list[float]:
        """
        Batch predict.

        Splits into chunks of max_batch_size and records latency statistics.

        Args:
            model: CrossEncoder or other batch-predictable model
            pairs: [(query, doc_text), ...] list

        Returns:
            list[float] score for each pair
        """
        if not pairs:
            return []

        t_start = time.time()

        try:
            # Split by max_batch_size for batch inference
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
                    queue_delay_ms=0.0,  # synchronous mode: no queue delay
                    inference_ms=inference_ms,
                )

            return all_scores

        except Exception as e:
            logger.error(f"Batch predict failed: {e}")
            inference_ms = (time.time() - t_start) * 1000
            with self._lock:
                self._stats.record_batch(len(pairs), 0.0, inference_ms)
            return [0.0] * len(pairs)

    def batch_predict_nli(self, model, pairs: list[tuple[str, str]]) -> list:
        """
        NLI batch inference (shared by Evidence Gate + Answer Gate).

        Args:
            model: NLI classification model
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
            logger.error(f"NLI batch predict failed: {e}")
            return [()] * len(pairs)

    def get_stats(self) -> dict:
        """
        Get batch processing statistics (for observability / monitoring).

        Returns:
            - batch_fill_rate: avg batch size / max batch size
            - queue_delay_p50/p99: batch queue delays
            - total_batches: total batch count
            - avg_pairs_per_batch: average pairs per batch
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
                "pending_count": 0,  # synchronous mode: no pending
            }
