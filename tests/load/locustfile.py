"""Locust 压测脚本 — 化妆品 RAG 系统（增强版：采集系统级缓存命中率 + 延迟分布）。"""

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from locust import HttpUser, between, events, task

logger = logging.getLogger(__name__)

# ─── 配置 ─────────────────────────────────────────────────────
REPORT_DIR = Path(__file__).resolve().parent.parent.parent / "reports" / "benchmark"
API_BASE = os.environ.get("BENCHMARK_API_BASE", "http://localhost:8000")
AUTH_TOKEN = os.environ.get("BENCHMARK_AUTH_TOKEN", "")

# 测试查询
QUERIES_REGULATION = [
    "化妆品中铅含量的限量标准是什么？",
    "防晒产品需要哪些法规认证？",
    "化妆品标签需要标注哪些成分信息？",
    "儿童化妆品有什么特殊规定？",
    "进口化妆品的备案流程是什么？",
    "化妆品功效宣称需要哪些证明材料？",
    "化妆品新原料注册备案流程？",
    "化妆品生产许可证办理条件？",
]

QUERIES_INGREDIENT = [
    "烟酰胺的安全浓度是多少？",
    "透明质酸钠有什么功效？",
    "水杨酸在化妆品中的使用限制？",
    "视黄醇的刺激性如何降低？",
    "熊果苷和曲酸哪个美白效果更好？",
    "神经酰胺和角鲨烷哪个修复效果好？",
    "维C衍生物和原型维C有什么区别？",
    "蓝铜肽的配伍禁忌有哪些？",
]

QUERIES_GENERAL = [
    "保湿面霜的常见成分有哪些？",
    "如何判断化妆品是否过期？",
    "敏感肌肤适合用什么类型的护肤品？",
    "防晒霜的SPF和PA值是什么意思？",
    "化妆品中的防腐剂有哪些？",
    "油性皮肤适合用哪种洁面产品？",
    "去角质产品应该多久用一次？",
    "面膜的正确使用频率是多少？",
]

ALL_QUERIES = QUERIES_REGULATION + QUERIES_INGREDIENT + QUERIES_GENERAL

# ─── 数据收集 ──────────────────────────────────────────────────


@dataclass
class LatencyStats:
    """延迟统计"""

    values: list[float] = field(default_factory=list)

    def add(self, ms: float) -> None:
        self.values.append(ms)

    def percentile(self, p: float) -> float:
        if not self.values:
            return 0.0
        sorted_vals = sorted(self.values)
        idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * p / 100)))
        return round(sorted_vals[idx], 2)

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p95(self) -> float:
        return self.percentile(95)

    @property
    def p99(self) -> float:
        return self.percentile(99)

    @property
    def avg(self) -> float:
        return round(sum(self.values) / len(self.values), 2) if self.values else 0.0

    @property
    def count(self) -> int:
        return len(self.values)

    @property
    def min(self) -> float:
        return round(min(self.values), 2) if self.values else 0.0

    @property
    def max(self) -> float:
        return round(max(self.values), 2) if self.values else 0.0


# 全局统计收集器（跨所有 Locust worker）
query_latencies = LatencyStats()
cache_hits = 0  # 占位：未来逐请求缓存命中追踪
cache_misses = 0  # 占位：未来逐请求缓存未命中追踪
errors = 0


def collect_stats():
    """从 /api/stats 端点采集系统级指标（替代逐请求缓存监控）。"""
    headers = {}
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
    try:
        resp = requests.get(f"{API_BASE}/api/stats", headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            cache_rates = data.get("cache_hit_rate", {})
            prefix_rate = data.get("prefix_cache_hit_rate", 0.0)
            return {
                "cache_hit_rate": cache_rates,
                "prefix_cache_hit_rate": prefix_rate,
                "system_uptime_seconds": data.get("uptime_seconds", 0),
                "full_stats": data,
            }
    except Exception as exc:
        logger.warning("Failed to collect system stats: %s", exc)
    return {}


# ─── 事件钩子：采集每次请求的延迟和缓存命中 ────────────────


@events.request.add_listener
def on_request(context, **kwargs):
    global errors
    response_time = kwargs.get("response_time", 0)
    response = kwargs.get("response", None)
    exception = kwargs.get("exception", None)

    if exception or (response and response.status_code >= 400):
        errors += 1
        return

    query_latencies.add(response_time)


# ─── Locust 用户行为 ──────────────────────────────────────────


class RAGUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.headers = {
            "Content-Type": "application/json",
        }
        if AUTH_TOKEN:
            self.headers["Authorization"] = f"Bearer {AUTH_TOKEN}"

    @task(40)
    def single_query(self):
        query = random.choice(ALL_QUERIES)  # noqa: S311 - randomized benchmark workload, not security token generation
        self.client.post(
            "/api/query",
            json={"query": query},
            headers=self.headers,
            name="/api/query",
        )

    @task(20)
    def stats_check(self):
        self.client.get("/api/stats", name="/api/stats")

    @task(10)
    def health_check(self):
        self.client.get("/api/health", name="/api/health")


# ─── 停止时生成报告 ────────────────────────────────────────────


def build_report(environment, system_stats) -> dict:
    """构建基准测试报告字典。"""
    total_errors = errors
    successful = query_latencies.count
    total_attempts = total_errors + successful
    return {
        "benchmark": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "duration_seconds": round(environment.runner.stats.total.time if environment.runner else 0, 2),
            "total_attempts": total_attempts,
            "successful_requests": successful,
            "concurrent_users": (environment.runner.target_user_count if environment.runner else 0),
            "api_base": API_BASE,
        },
        "latency_ms": {
            "avg": query_latencies.avg,
            "min": query_latencies.min,
            "max": query_latencies.max,
            "p50": query_latencies.p50,
            "p95": query_latencies.p95,
            "p99": query_latencies.p99,
        },
        "cache": {
            "client_cache_hits": cache_hits,
            "client_cache_misses": cache_misses,
            "client_cache_hit_rate_pct": round(cache_hits / max(cache_hits + cache_misses, 1) * 100, 2),
        },
        "system_stats": system_stats,
        "errors": {
            "total": errors,
            "error_rate_pct": round(total_errors / max(total_attempts, 1) * 100, 2),
        },
    }


def write_report(report: dict) -> Path:
    """将报告写入 JSON 文件，返回文件路径。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"benchmark_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report_path


def aggregate_history() -> dict:
    """统计 reports/benchmark/ 下所有历史报告的汇总数据。"""
    existing_reports = sorted(REPORT_DIR.glob("benchmark_*.json"))
    total_reqs = 0
    total_errs = 0
    for rp in existing_reports:
        try:
            with open(rp) as f:
                data = json.load(f)
            total_reqs += data.get("benchmark", {}).get("total_attempts", 0)
            total_errs += data.get("errors", {}).get("total", 0)
        except Exception as exc:
            logger.debug("Could not collect server stats for report: %s", exc)
    return {
        "report_count": len(existing_reports),
        "total_requests_historical": total_reqs,
        "total_errors_historical": total_errs,
    }


def print_summary(report: dict, system_stats: dict, report_path: Path, history: dict):
    """打印格式化的控制台摘要。"""
    print(f"\n{'=' * 60}")
    print(f"📊 Benchmark 报告已保存: {report_path}")
    print(f"{'=' * 60}")
    bm = report["benchmark"]
    print("  运行参数:")
    print(f"    并发用户数: {bm['concurrent_users']}")
    print(f"    运行时长: {bm['duration_seconds']:.0f}s")
    print(f"    总请求: {bm['total_attempts']} (成功: {bm['successful_requests']}, 失败: {report['errors']['total']})")
    print("  延迟 (ms):")
    lat = report["latency_ms"]
    print(f"    Avg: {lat['avg']} | P50: {lat['p50']} | P95: {lat['p95']} | P99: {lat['p99']}")
    print("  缓存:")
    cache_rates = system_stats.get("cache_hit_rate", {})
    l1 = cache_rates.get("L1", "N/A")
    l2 = cache_rates.get("L2", "N/A")
    prefix = system_stats.get("prefix_cache_hit_rate", "N/A")
    print(f"    系统 - L1: {l1} | L2: {l2}")
    print(f"    Prefix Cache 命中率: {prefix}")
    print(f"  错误率: {report['errors']['error_rate_pct']}%")
    print(f"{'=' * 60}")
    print(f"📁 历史报告: {REPORT_DIR} 下共有 {history['report_count']} 份报告")
    print(f"   累计请求: {history['total_requests_historical']} | 累计错误: {history['total_errors_historical']}")
    print(f"{'=' * 60}")


@events.quit.add_listener
def generate_report(environment, **kwargs):
    """压测结束时生成 JSON 报告（增强版：包含系统级缓存命中率 + 延迟分布）。"""
    system_stats = collect_stats()
    report = build_report(environment, system_stats)
    report_path = write_report(report)
    history = aggregate_history()
    print_summary(report, system_stats, report_path, history)
