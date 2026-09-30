"""
MetricsCollector -- 全链路指标收集

收集 PRD §12 要求的关键指标：
- L1/L2 命中率（按权限分区统计）
- Rewrite 延迟/成功率
- Evidence Gate 分数分布
- KV Pressure 实时值
- 降级触发次数
- Rerank Batch Aggregator 指标
- NLI 矛盾比例 (nli_contradiction_rate)
- BLIP 触发率 (blip_trigger_rate)
- CLIP 同步超时率 (clip_sync_timeout_rate)
- Redis 降级模式状态 (redis_degraded_mode, 0/1)
- Cache 版本切换次数 (cache_version_switch_count)
- Batch 填充率 (rerank_batch_fill_rate)
- 批处理排队延迟 P99 (rerank_batch_queue_latency_p99)
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    指标收集器（完整版，对齐 otel_tracer.py）

    收集 PRD §12 要求的关键指标：
    - L1/L2/L2_SESSION 命中率（按权限分区统计）
    - Rewrite 延迟/成功率/降级率
    - Evidence Gate 分数分布
    - KV Pressure 实时值 + 有效并发数 + 估算 QPS
    - NLI 矛盾比例 (nli_contradiction_rate)
    - BLIP 触发率 (blip_trigger_rate)
    - CLIP 同步超时率 (clip_sync_timeout_rate)
    - Prefix Cache 命中率
    - Redis 降级模式状态 (redis_degraded_mode, gauge 0/1)
    - Cache 版本切换计数 (cache_version_switch_count)
    - Admission Control 拒绝/排队次数
    - 降级触发次数
    - Rerank Batch Aggregator 指标
      - Batch 填充率 (rerank_batch_fill_rate)
      - 批处理排队延迟 P99 (rerank_batch_queue_latency_p99)
    """

    def __init__(self):
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._start_time = time.time()
        # ── PRD §12: 预初始化专用 gauge，确保 get_stats / prometheus 有初始值 ──
        self._gauges.setdefault("nli_contradiction_rate", 0.0)
        self._gauges.setdefault("blip_trigger_rate", 0.0)
        self._gauges.setdefault("clip_sync_timeout_rate", 0.0)
        self._gauges.setdefault("redis_degraded_mode", 0.0)  # 0=正常, 1=降级
        self._gauges.setdefault("cache_version_switch_count", 0.0)
        self._gauges.setdefault("rerank_batch_fill_rate", 0.0)
        self._gauges.setdefault("rerank_batch_queue_latency_p99", 0.0)
        logger.info("MetricsCollector 初始化完成")

    def increment(self, name: str, value: int = 1):
        """递增计数器"""
        self._counters[name] += value

    def set_gauge(self, name: str, value: float):
        """设置仪表盘值"""
        self._gauges[name] = value

    def observe_histogram(self, name: str, value: float):
        """记录直方图值"""
        self._histograms[name].append(value)
        if len(self._histograms[name]) > 1000:
            self._histograms[name] = self._histograms[name][-1000:]

    # ── PRD §12 专用指标更新方法 ──────────────────────────────────────

    def update_rate_gauges(self):
        """
        根据当前 counter 值重算 rate 类 gauge，供 record_request 结束后或定时任务调用。

        更新: nli_contradiction_rate, blip_trigger_rate, clip_sync_timeout_rate
        """
        # nli_contradiction_rate
        nli_total = max(self._counters.get("nli.total", 0), 1)
        self._gauges["nli_contradiction_rate"] = round(self._counters.get("nli.contradiction_high", 0) / nli_total, 6)
        # blip_trigger_rate
        blip_total = max(self._counters.get("blip.total", 0), 1)
        self._gauges["blip_trigger_rate"] = round(self._counters.get("blip.triggered", 0) / blip_total, 6)
        # clip_sync_timeout_rate
        clip_sync_total = max(self._counters.get("clip.total_sync", 0), 1)
        self._gauges["clip_sync_timeout_rate"] = round(self._counters.get("clip.sync_timeout", 0) / clip_sync_total, 6)

    def set_redis_degraded_mode(self, degraded: bool):
        """
        设置 Redis 降级模式状态 (0/1 gauge)。

        需要从 cache-service 的 Redis 连接状态 hook 调用。
        当 Redis 断连或 read-only 模式时传入 True。
        """
        self._gauges["redis_degraded_mode"] = 1.0 if degraded else 0.0

    def increment_cache_version_switch(self):
        """
        Cache 版本切换计数 (counter → 同步更新 gauge 供 Prometheus 直接读取)。

        需要从 cache-service 的 epoch/version 切换逻辑 hook 调用。
        """
        self.increment("cache.epoch_switches")
        self._gauges["cache_version_switch_count"] = float(self._counters.get("cache.epoch_switches", 0))

    def set_rerank_batch_fill_rate(self, fill_rate: float):
        """
        Rerank Batch 填充率 gauge (0.0 ~ 1.0)。

        需要从 retrieval/rerank_batch_aggregator.py 的 _build_batch() hook 调用。
        fill_rate = 实际填充的 slot 数 / 最大 batch size。
        当前为占位：未接入时返回 0。
        """
        self._gauges["rerank_batch_fill_rate"] = round(fill_rate, 6)

    def set_rerank_batch_queue_latency_p99(self, latency_ms: float):
        """
        批处理排队延迟 P99 gauge (毫秒)。

        需要从 retrieval/rerank_batch_aggregator.py 的排队等待时间统计 hook 调用。
        当前为占位：未接入时返回 0。
        """
        self._gauges["rerank_batch_queue_latency_p99"] = round(latency_ms, 2)

    def collect_rerank_batch_metrics(self) -> dict[str, float]:
        """
        从 RerankBatchAggregator 采集 batch 指标。

        通过检索 RerankBatchAggregator 单例获取实时 batch 填充率和排队延迟。
        若 aggregator 未初始化则优雅降级返回 0。
        """
        try:
            from retrieval.rerank_batch_aggregator import RerankBatchAggregator

            aggregator = RerankBatchAggregator()
            stats = aggregator.get_stats()
            fill_rate = stats.get("batch_fill_rate", 0.0)
            queue_latency_p99 = stats.get("queue_delay_p99", 0.0)
        except Exception:
            fill_rate = 0.0
            queue_latency_p99 = 0.0
        self.set_rerank_batch_fill_rate(fill_rate)
        self.set_rerank_batch_queue_latency_p99(queue_latency_p99)
        return {
            "rerank_batch_fill_rate": fill_rate,
            "rerank_batch_queue_latency_p99": queue_latency_p99,
        }

    def collect_nli_contradiction_rate(self) -> float:
        """
        从 record_request 计数器聚合获取 NLI 矛盾比例。

        数据来源：record_request 中 update_rate_gauges() 自动更新 gauge。
        """
        return self._gauges.get("nli_contradiction_rate", 0.0)

    def collect_blip_trigger_rate(self) -> float:
        """
        占位方法 — 从 BLIP 服务采集触发率。

        TODO: 需要从 models/blip_service.py 的调用统计 hook:
              - 统计 blip_triggered=True 的比例
              - 当 record_request 已在逐次统计，此方法可从 aggregator 获取全局比例
        当前返回 0。
        """
        return self._gauges.get("blip_trigger_rate", 0.0)

    def collect_clip_sync_timeout_rate(self) -> float:
        """
        占位方法 — 从 CLIP 服务采集同步超时率。

        TODO: 需要从 retrieval/parallel_recall.py 或 clip_client 的超时统计 hook:
              - 统计 clip_sync_timeout=True 的比例
              - 当 record_request 已在逐次统计，此方法可从 aggregator 获取全局比例
        当前返回 0。
        """
        return self._gauges.get("clip_sync_timeout_rate", 0.0)

    def record_request(self, ctx):
        """
        记录单次请求的指标

        Args:
            ctx: RequestContext (dict or object with the expected attributes)
        """
        # ── 缓存命中率 ──────────────────────────────────────────────
        cache_hit_level = getattr(ctx, "cache_hit_level", None) or (
            ctx.get("cache_hit_level") if isinstance(ctx, dict) else None
        )
        if cache_hit_level:
            self.increment(f"cache.hit.{cache_hit_level}")
        self.increment("cache.total")

        # ── Rewrite 成功率 / 降级率 ─────────────────────────────────
        rewrite_result = getattr(ctx, "rewrite_result", None) or (
            ctx.get("rewrite_result") if isinstance(ctx, dict) else None
        )
        if rewrite_result:
            self.increment("rewrite.success")
            fallback = getattr(rewrite_result, "fallback", None) or (
                rewrite_result.get("fallback") if isinstance(rewrite_result, dict) else None
            )
            if fallback:
                self.increment("rewrite.fallback")
            # Rewrite 延迟
            rewrite_latency = getattr(ctx, "rewrite_latency_ms", None) or (
                ctx.get("rewrite_latency_ms") if isinstance(ctx, dict) else None
            )
            if rewrite_latency:
                self.observe_histogram("latency.rewrite", rewrite_latency)
        else:
            self.increment("rewrite.fail")

        # ── Evidence Gate 分数 + 决策 ───────────────────────────────
        evidence_result = getattr(ctx, "evidence_result", None) or (
            ctx.get("evidence_result") if isinstance(ctx, dict) else None
        )
        if evidence_result:
            ev_score = getattr(evidence_result, "evidence_score", None) or (
                evidence_result.get("evidence_score") if isinstance(evidence_result, dict) else None
            )
            if ev_score is not None:
                self.observe_histogram("evidence.score", ev_score)
            decision = getattr(evidence_result, "decision", None) or (
                evidence_result.get("decision") if isinstance(evidence_result, dict) else None
            )
            if decision:
                self.increment(f"evidence.decision.{decision}")

        # ── NLI 矛盾比例（Answer Gate）──────────────────────────────
        answer_gate = getattr(ctx, "answer_gate_result", None) or (
            ctx.get("answer_gate_result") if isinstance(ctx, dict) else None
        )
        if answer_gate:
            nli_contra = getattr(answer_gate, "nli_contradiction_score", None) or (
                answer_gate.get("nli_contradiction_score") if isinstance(answer_gate, dict) else None
            )
            if nli_contra is not None:
                self.observe_histogram("nli.contradiction_score", nli_contra)
                if nli_contra > 0.5:
                    self.increment("nli.contradiction_high")
            self.increment("nli.total")

        # ── BLIP 触发 ──────────────────────────────────────────────
        blip_triggered = getattr(ctx, "blip_triggered", None) or (
            ctx.get("blip_triggered") if isinstance(ctx, dict) else None
        )
        self.increment("blip.total")
        if blip_triggered:
            self.increment("blip.triggered")

        # ── CLIP 同步超时率 / 异步补充命中率（PRD §12）──────────────
        clip_use = getattr(ctx, "clip_use", None) or (ctx.get("clip_use") if isinstance(ctx, dict) else None)
        self.increment("clip.total_queries")
        if clip_use:
            self.increment("clip.image_queries")  # 图像 query 计数（用于 image_query_ratio）

        clip_sync_timeout = getattr(ctx, "clip_sync_timeout", None) or (
            ctx.get("clip_sync_timeout") if isinstance(ctx, dict) else None
        )
        self.increment("clip.total_sync")
        if clip_sync_timeout:
            self.increment("clip.sync_timeout")

        clip_async_hit = getattr(ctx, "clip_async_hit", None) or (
            ctx.get("clip_async_hit") if isinstance(ctx, dict) else None
        )
        self.increment("clip.total_async")
        if clip_async_hit:
            self.increment("clip.async_hit")

        # ── CLIP Rerank 贡献度（GAP-20: PRD §4.5/§12.2 离线权重统计）──
        clip_contribution = getattr(ctx, "clip_contribution_ratio", None) or (
            ctx.get("clip_contribution_ratio") if isinstance(ctx, dict) else None
        )
        if clip_contribution is not None:
            self.observe_histogram("clip.rerank_contribution_ratio", clip_contribution)

        # ── Retrieval Agreement Score 分布（PRD §12）───────────────
        agreement_score = getattr(ctx, "retrieval_agreement_score", None) or (
            ctx.get("retrieval_agreement_score") if isinstance(ctx, dict) else None
        )
        if agreement_score is not None and agreement_score > 0:
            self.observe_histogram("retrieval.agreement_score", agreement_score)

        # ── 降级 ────────────────────────────────────────────────────
        degraded = getattr(ctx, "degraded", None) or (ctx.get("degraded") if isinstance(ctx, dict) else None)
        if degraded:
            self.increment("degradation.total")

        # ── Admission Control ───────────────────────────────────────
        admission_admitted = getattr(ctx, "admission_admitted", None) or (
            ctx.get("admission_admitted") if isinstance(ctx, dict) else None
        )
        if admission_admitted is not None:
            self.increment("admission.total")
            if admission_admitted:
                self.increment("admission.admitted")
            else:
                self.increment("admission.rejected")

        # ── KV Pressure + 有效并发数 ────────────────────────────────
        kv_pressure = getattr(ctx, "kv_pressure_at_entry", None) or (
            ctx.get("kv_pressure_at_entry") if isinstance(ctx, dict) else None
        )
        if kv_pressure and kv_pressure > 0:
            self.set_gauge("kv_pressure", kv_pressure)

        effective_concurrency = getattr(ctx, "effective_concurrency", None) or (
            ctx.get("effective_concurrency") if isinstance(ctx, dict) else None
        )
        if effective_concurrency is not None:
            self.set_gauge("effective_concurrency", effective_concurrency)

        # ── Prefix Cache 命中/未命中 ───────────────────────────────
        prefix_cache_hit = getattr(ctx, "prefix_cache_hit", None)
        if isinstance(ctx, dict):
            prefix_cache_hit = ctx.get("prefix_cache_hit")
        if prefix_cache_hit is True:
            self.increment("prefix_cache.hit")
        elif prefix_cache_hit is False:
            self.increment("prefix_cache.miss")

        # ── Redis 降级 ──────────────────────────────────────────────
        redis_degraded = getattr(ctx, "redis_degraded", None) or (
            ctx.get("redis_degraded") if isinstance(ctx, dict) else None
        )
        if redis_degraded:
            self.increment("redis.degraded_events")

        # ── Cache 版本切换 ─────────────────────────────────────────
        cache_epoch_switch = getattr(ctx, "cache_epoch_switch", None) or (
            ctx.get("cache_epoch_switch") if isinstance(ctx, dict) else None
        )
        if cache_epoch_switch:
            self.increment_cache_version_switch()

        # ── 各阶段延迟 ─────────────────────────────────────────────
        stage_timings = getattr(ctx, "stage_timings", None) or (
            ctx.get("stage_timings") if isinstance(ctx, dict) else {}
        )
        if stage_timings:
            for stage, duration_ms in stage_timings.items():
                self.observe_histogram(f"latency.{stage}", duration_ms)

        # ── 刷新 rate 类 gauge ────────────────────────────────────
        self.update_rate_gauges()

    def get_counter(self, name: str) -> int:
        return self._counters.get(name, 0)

    def get_gauge(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    def collect_admission_metrics(self) -> str:
        """
        PRD §12 + §5.2.6: 收集 Admission Control 指标并格式化为 Prometheus 文本。

        调用 KVAdmissionControl.get_metrics() 获取运行时指标，
        返回包含所有 admission 相关 gauge/counter 的 Prometheus text block。
        可拼接到 to_prometheus_text() 输出中。

        Returns:
            Prometheus text format string
        """
        try:
            from admission.kv_admission import KVAdmissionControl

            adm = KVAdmissionControl()
            m = adm.get_metrics()
        except Exception as e:
            logger.warning(f"Failed to collect admission metrics: {e}")
            return ""

        lines = []

        lines.append("# HELP rag_admission_effective_concurrency Current active request count")
        lines.append("# TYPE rag_admission_effective_concurrency gauge")
        lines.append(f"rag_admission_effective_concurrency {m['effective_concurrency']}")

        lines.append("# HELP rag_admission_max_num_seqs Dynamically computed max_num_seqs")
        lines.append("# TYPE rag_admission_max_num_seqs gauge")
        lines.append(f"rag_admission_max_num_seqs {m['max_num_seqs']}")

        lines.append("# HELP rag_admission_kv_pressure KV cache pressure ratio")
        lines.append("# TYPE rag_admission_kv_pressure gauge")
        lines.append(f"rag_admission_kv_pressure {m['kv_pressure']}")

        lines.append("# HELP rag_admission_kv_budget_bytes KV cache budget in bytes")
        lines.append("# TYPE rag_admission_kv_budget_bytes gauge")
        lines.append(f"rag_admission_kv_budget_bytes {m['kv_budget_bytes']}")

        lines.append("# HELP rag_admission_kv_used_bytes Currently used KV cache in bytes")
        lines.append("# TYPE rag_admission_kv_used_bytes gauge")
        lines.append(f"rag_admission_kv_used_bytes {m['kv_used_bytes']}")

        lines.append("# HELP rag_admission_throughput_qps Estimated throughput QPS")
        lines.append("# TYPE rag_admission_throughput_qps gauge")
        lines.append(f"rag_admission_throughput_qps {m['throughput_qps']}")

        lines.append("# HELP rag_admission_total_total Total admission control decisions")
        lines.append("# TYPE rag_admission_total_total counter")
        lines.append(f"rag_admission_total_total {m['admission_total']}")

        lines.append("# HELP rag_admission_rejected_total Total admission rejections")
        lines.append("# TYPE rag_admission_rejected_total counter")
        lines.append(f"rag_admission_rejected_total {m['admission_rejected']}")

        lines.append("# HELP rag_admission_queued_total Total admission queue events")
        lines.append("# TYPE rag_admission_queued_total counter")
        lines.append(f"rag_admission_queued_total {m['admission_queued']}")

        return "\n".join(lines) + "\n"

    @staticmethod
    def _percentile(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        idx = int(len(sorted_vals) * p)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    def get_stats(self) -> dict:
        """获取统计摘要（对齐 PRD §12 全部指标）"""
        total_req = max(self._counters.get("cache.total", 1), 1)
        rewrite_total = max(
            self._counters.get("rewrite.success", 0) + self._counters.get("rewrite.fail", 0),
            1,
        )
        nli_total = max(self._counters.get("nli.total", 1), 1)
        blip_total = max(self._counters.get("blip.total", 1), 1)
        prefix_total = max(
            self._counters.get("prefix_cache.hit", 0) + self._counters.get("prefix_cache.miss", 0),
            1,
        )
        admission_total = max(self._counters.get("admission.total", 1), 1)

        stats = {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            # PRD §12: 缓存命中率
            "cache_hit_rate": {
                "L1": self._counters.get("cache.hit.L1", 0) / total_req,
                "L2": self._counters.get("cache.hit.L2", 0) / total_req,
                "L2_SESSION": self._counters.get("cache.hit.L2_SESSION", 0) / total_req,
            },
            # PRD §12: Rewrite 指标
            "rewrite_fallback_rate": self._counters.get("rewrite.fallback", 0) / rewrite_total,
            # PRD §12: NLI 矛盾比例
            "nli_contradiction_rate": self._counters.get("nli.contradiction_high", 0) / nli_total,
            # PRD §12: BLIP 触发率
            "blip_trigger_rate": self._counters.get("blip.triggered", 0) / blip_total,
            # PRD §12: CLIP 同步超时率
            "clip_sync_timeout_rate": self._counters.get("clip.sync_timeout", 0)
            / max(self._counters.get("clip.total_sync", 1), 1),
            "clip_async_hit_rate": self._counters.get("clip.async_hit", 0)
            / max(self._counters.get("clip.total_async", 1), 1),
            # PRD §4.5: 图像 query 占比（image query ratio）
            "image_query_ratio": self._counters.get("clip.image_queries", 0)
            / max(self._counters.get("clip.total_queries", 1), 1),
            # PRD §12: Prefix Cache 命中率
            "prefix_cache_hit_rate": self._counters.get("prefix_cache.hit", 0) / prefix_total,
            # PRD §12: Admission Control
            "admission": {
                "total": self._counters.get("admission.total", 0),
                "admitted": self._counters.get("admission.admitted", 0),
                "rejected": self._counters.get("admission.rejected", 0),
                "reject_rate": self._counters.get("admission.rejected", 0) / admission_total,
            },
            # PRD §12: Redis 降级
            "redis_degradation_count": self._counters.get("redis.degraded_events", 0),
            # PRD §12: Cache 版本切换
            "cache_epoch_switches": self._counters.get("cache.epoch_switches", 0),
            # PRD §12: 降级触发次数
            "degradation_count": self._counters.get("degradation.total", 0),
            # PRD §12: 专用 gauge 快照
            "nli_contradiction_rate_gauge": self._gauges.get("nli_contradiction_rate", 0.0),
            "blip_trigger_rate_gauge": self._gauges.get("blip_trigger_rate", 0.0),
            "clip_sync_timeout_rate_gauge": self._gauges.get("clip_sync_timeout_rate", 0.0),
            "redis_degraded_mode": self._gauges.get("redis_degraded_mode", 0.0),
            "cache_version_switch_count": self._gauges.get("cache_version_switch_count", 0.0),
            "rerank_batch_fill_rate": self._gauges.get("rerank_batch_fill_rate", 0.0),
            "rerank_batch_queue_latency_p99": self._gauges.get("rerank_batch_queue_latency_p99", 0.0),
            # 延迟分位数
            "latency_percentiles": {},
        }

        for name, values in self._histograms.items():
            stats["latency_percentiles"][name] = {
                "p50": round(self._percentile(values, 0.5), 2),
                "p95": round(self._percentile(values, 0.95), 2),
                "p99": round(self._percentile(values, 0.99), 2),
                "count": len(values),
            }

        return stats

    def get_prometheus_metrics(self) -> str:
        """别名：供微服务 /metrics 端点调用（与 to_prometheus_text 相同）"""
        return self.to_prometheus_text()

    def to_prometheus_text(self) -> str:
        """
        生成 Prometheus text format 指标输出。
        供 /metrics 端点返回给 Prometheus scrape。
        """
        lines = []
        lines.append("# HELP rag_cache_hit_rate Cache hit rate by level")
        lines.append("# TYPE rag_cache_hit_rate gauge")
        total_req = max(self._counters.get("cache.total", 1), 1)
        for level in ("L1", "L2", "L2_SESSION"):
            val = self._counters.get(f"cache.hit.{level}", 0) / total_req
            lines.append(f'rag_cache_hit_rate{{level="{level}"}} {val:.6f}')

        lines.append("# HELP rag_rewrite_fallback_rate Rewrite fallback rate")
        lines.append("# TYPE rag_rewrite_fallback_rate gauge")
        rewrite_total = max(self._counters.get("rewrite.success", 0) + self._counters.get("rewrite.fail", 0), 1)
        lines.append(f"rag_rewrite_fallback_rate {self._counters.get('rewrite.fallback', 0) / rewrite_total:.6f}")

        lines.append("# HELP rag_kv_pressure KV cache pressure gauge")
        lines.append("# TYPE rag_kv_pressure gauge")
        lines.append(f"rag_kv_pressure {self._gauges.get('kv_pressure', 0.0):.6f}")

        lines.append("# HELP rag_effective_concurrency Current effective concurrency")
        lines.append("# TYPE rag_effective_concurrency gauge")
        lines.append(f"rag_effective_concurrency {self._gauges.get('effective_concurrency', 0.0):.1f}")

        lines.append("# HELP rag_nli_contradiction_rate NLI contradiction rate")
        lines.append("# TYPE rag_nli_contradiction_rate gauge")
        nli_total = max(self._counters.get("nli.total", 1), 1)
        lines.append(f"rag_nli_contradiction_rate {self._counters.get('nli.contradiction_high', 0) / nli_total:.6f}")

        lines.append("# HELP rag_blip_trigger_rate BLIP trigger rate")
        lines.append("# TYPE rag_blip_trigger_rate gauge")
        blip_total = max(self._counters.get("blip.total", 1), 1)
        lines.append(f"rag_blip_trigger_rate {self._counters.get('blip.triggered', 0) / blip_total:.6f}")

        lines.append("# HELP rag_clip_sync_timeout_rate CLIP sync timeout rate")
        lines.append("# TYPE rag_clip_sync_timeout_rate gauge")
        clip_sync_total = max(self._counters.get("clip.total_sync", 1), 1)
        lines.append(f"rag_clip_sync_timeout_rate {self._counters.get('clip.sync_timeout', 0) / clip_sync_total:.6f}")

        lines.append("# HELP rag_clip_async_hit_rate CLIP async supplement hit rate")
        lines.append("# TYPE rag_clip_async_hit_rate gauge")
        clip_async_total = max(self._counters.get("clip.total_async", 1), 1)
        lines.append(f"rag_clip_async_hit_rate {self._counters.get('clip.async_hit', 0) / clip_async_total:.6f}")

        lines.append("# HELP rag_image_query_ratio Image query ratio (queries activated CLIP / total queries)")
        lines.append("# TYPE rag_image_query_ratio gauge")
        clip_query_total = max(self._counters.get("clip.total_queries", 1), 1)
        lines.append(f"rag_image_query_ratio {self._counters.get('clip.image_queries', 0) / clip_query_total:.6f}")

        lines.append("# HELP rag_prefix_cache_hit_rate Prefix cache hit rate")
        lines.append("# TYPE rag_prefix_cache_hit_rate gauge")
        prefix_total = max(self._counters.get("prefix_cache.hit", 0) + self._counters.get("prefix_cache.miss", 0), 1)
        lines.append(f"rag_prefix_cache_hit_rate {self._counters.get('prefix_cache.hit', 0) / prefix_total:.6f}")

        lines.append("# HELP rag_admission_rejected_total Admission control rejections")
        lines.append("# TYPE rag_admission_rejected_total counter")
        lines.append(f"rag_admission_rejected_total {self._counters.get('admission.rejected', 0)}")

        lines.append("# HELP rag_redis_degradation_total Redis degradation events")
        lines.append("# TYPE rag_redis_degradation_total counter")
        lines.append(f"rag_redis_degradation_total {self._counters.get('redis.degraded_events', 0)}")

        lines.append("# HELP rag_cache_epoch_switches Cache version epoch switch count")
        lines.append("# TYPE rag_cache_epoch_switches counter")
        lines.append(f"rag_cache_epoch_switches {self._counters.get('cache.epoch_switches', 0)}")

        lines.append("# HELP rag_degradation_total Degradation trigger count")
        lines.append("# TYPE rag_degradation_total counter")
        lines.append(f"rag_degradation_total {self._counters.get('degradation.total', 0)}")

        # ── PRD §12: 新增专用 gauge 指标 ──────────────────────────
        lines.append("# HELP rag_nli_contradiction_rate_gauge NLI contradiction rate (dedicated gauge)")
        lines.append("# TYPE rag_nli_contradiction_rate_gauge gauge")
        lines.append(f"rag_nli_contradiction_rate_gauge {self._gauges.get('nli_contradiction_rate', 0.0):.6f}")

        lines.append("# HELP rag_blip_trigger_rate_gauge BLIP trigger rate (dedicated gauge)")
        lines.append("# TYPE rag_blip_trigger_rate_gauge gauge")
        lines.append(f"rag_blip_trigger_rate_gauge {self._gauges.get('blip_trigger_rate', 0.0):.6f}")

        lines.append("# HELP rag_clip_sync_timeout_rate_gauge CLIP sync timeout rate (dedicated gauge)")
        lines.append("# TYPE rag_clip_sync_timeout_rate_gauge gauge")
        lines.append(f"rag_clip_sync_timeout_rate_gauge {self._gauges.get('clip_sync_timeout_rate', 0.0):.6f}")

        lines.append("# HELP rag_redis_degraded_mode Redis degraded mode status (0=normal, 1=degraded)")
        lines.append("# TYPE rag_redis_degraded_mode gauge")
        lines.append(f"rag_redis_degraded_mode {self._gauges.get('redis_degraded_mode', 0.0):.1f}")

        lines.append("# HELP rag_cache_version_switch_count Cache version epoch switch count (dedicated gauge)")
        lines.append("# TYPE rag_cache_version_switch_count gauge")
        lines.append(f"rag_cache_version_switch_count {self._gauges.get('cache_version_switch_count', 0.0):.1f}")

        lines.append("# HELP rag_rerank_batch_fill_rate Rerank batch fill rate (0.0~1.0)")
        lines.append("# TYPE rag_rerank_batch_fill_rate gauge")
        lines.append(f"rag_rerank_batch_fill_rate {self._gauges.get('rerank_batch_fill_rate', 0.0):.6f}")

        lines.append("# HELP rag_rerank_batch_queue_latency_p99 Rerank batch queue latency P99 in ms")
        lines.append("# TYPE rag_rerank_batch_queue_latency_p99 gauge")
        lines.append(
            f"rag_rerank_batch_queue_latency_p99 {self._gauges.get('rerank_batch_queue_latency_p99', 0.0):.2f}"
        )

        # ── PRD §12 + §5.2.6: Admission Control 实时指标 ──────────
        admission_block = self.collect_admission_metrics()
        if admission_block:
            lines.append(admission_block.strip())

        # 直方图 → summary
        for name, values in self._histograms.items():
            if not values:
                continue
            safe_name = name.replace(".", "_")
            lines.append(f"# HELP rag_{safe_name} Histogram for {name}")
            lines.append(f"# TYPE rag_{safe_name} summary")
            lines.append(f'rag_{{quantile="0.5"}} {self._percentile(values, 0.5):.2f}')
            lines.append(f'rag_{{quantile="0.95"}} {self._percentile(values, 0.95):.2f}')
            lines.append(f'rag_{{quantile="0.99"}} {self._percentile(values, 0.99):.2f}')

        return "\n".join(lines) + "\n"
