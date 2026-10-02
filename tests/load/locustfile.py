"""Locust 压测脚本 — 化妆品 RAG 系统（增强版：采集系统级缓存命中率 + 延迟分布）。"""

import logging
import os
import random
from pathlib import Path

import requests
from locust import HttpUser, between, events, task

from benchmarks.performance import (
    LatencyStats,
    RequestTally,
    Workload,
    build_environment,
    new_run_id,
    write_artifact,
)
from benchmarks.performance import build_report as build_performance_report

logger = logging.getLogger(__name__)

# Repository-root-relative artifact location, following the retrieval benchmark
# convention. Generated runs are git-ignored; only the README is tracked.
ARTIFACT_ROOT = Path(__file__).resolve().parents[2] / "artifacts" / "performance"

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

# LatencyStats 来自 benchmarks.performance，与 performance artifact 共用同一套
# 「未测量即 None」规则，避免两处实现漂移。
query_latencies = LatencyStats()
cache_hits = 0  # 占位：未来逐请求缓存命中追踪
cache_misses = 0  # 占位：未来逐请求缓存未命中追踪
errors = 0
rate_limited = 0
observed_users = 0
endpoint_counts: dict[str, dict[str, int]] = {}
status_counts: dict[str, int] = {}


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
    global errors, rate_limited, observed_users
    response_time = kwargs.get("response_time", 0)
    response = kwargs.get("response", None)
    exception = kwargs.get("exception", None)
    name = kwargs.get("name") or (response.request.path if response is not None and response.request else None)

    user_count = getattr(context, "user_count", None)
    if isinstance(user_count, int):
        observed_users = max(observed_users, user_count)

    status_code = response.status_code if response is not None else None
    if name:
        endpoint_counts.setdefault(name, {"total": 0, "success": 0, "failure": 0})
        endpoint_counts[name]["total"] += 1
        if status_code == 429:
            rate_limited += 1

    if exception or (response is not None and status_code is not None and status_code >= 400):
        errors += 1
        if name:
            endpoint_counts[name]["failure"] += 1
        if status_code is not None:
            status_counts[str(status_code)] = status_counts.get(str(status_code), 0) + 1
        if status_code is not None and status_code < 500:
            # A 4xx still carries a real latency; excluding it would bias the
            # reported percentiles toward the slow path only.
            if response_time and response_time > 0:
                query_latencies.add(response_time)
        return

    if name:
        endpoint_counts[name]["success"] += 1
    if status_code is not None:
        status_counts[str(status_code)] = status_counts.get(str(status_code), 0) + 1
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


# ─── 停止时生成 performance evidence artifact ───────────────────────


def _preflight() -> str | None:
    """Return a ``blocked_reason`` when the target cannot be measured.

    The API contract requires a bearer token on every endpoint except
    ``/api/health``. Running the workload without one would measure 401s and
    report them as a fast, healthy service, so a missing token blocks the run
    instead of producing a misleading number.
    """
    try:
        health = requests.get(f"{API_BASE}/api/health", timeout=10)
    except Exception as exc:
        return f"api_unreachable: {type(exc).__name__}"
    if health.status_code != 200:
        return f"api_health_status_{health.status_code}"
    if not AUTH_TOKEN:
        return "missing_auth_token: /api/stats and /api/query require a bearer token"
    try:
        stats = requests.get(
            f"{API_BASE}/api/stats",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            timeout=10,
        )
    except Exception as exc:
        return f"api_stats_unreachable: {type(exc).__name__}"
    if stats.status_code != 200:
        return f"api_stats_status_{stats.status_code}"
    return None


def build_workload(environment) -> Workload:
    runner = getattr(environment, "runner", None)
    return Workload(
        users=getattr(runner, "target_user_count", None) if runner else None,
        spawn_rate=float(os.environ.get("BENCHMARK_SPAWN_RATE") or 0) or None,
        duration_seconds=float(os.environ.get("BENCHMARK_DURATION_SECONDS") or 0) or None,
        target_base=API_BASE,
        endpoints=tuple(sorted(endpoint_counts)),
        authenticated=bool(AUTH_TOKEN),
    )


def build_report(environment) -> dict:
    """Build the performance artifact payload for the finished run."""
    runner = getattr(environment, "runner", None)
    duration = None
    if runner is not None and getattr(runner, "stats", None) is not None:
        total = getattr(runner.stats, "total", None)
        if total is not None:
            duration = getattr(total, "time", None) or None

    successful = query_latencies.count
    total_attempts = errors + successful
    requests = RequestTally(total=total_attempts, success=successful, failure=errors)
    workload = build_workload(environment)

    limitations = [
        f"status class mix observed: {status_counts or 'none'}",
        f"429 responses observed: {rate_limited}",
    ]
    if not AUTH_TOKEN:
        limitations.append("no bearer token was supplied; authenticated endpoints were not measured")

    artifact = write_artifact(
        ARTIFACT_ROOT / new_run_id(),
        kind="locust",
        blocked_reason=_preflight(),
        workload=workload,
        latency_samples=list(query_latencies.values),
        requests=requests,
        duration_seconds=duration,
        observed_users=observed_users or None,
        limitations=limitations,
        repo_root=str(Path(__file__).resolve().parents[2]),
    )
    artifact["endpoint_counts"] = dict(endpoint_counts)
    artifact["status_counts"] = dict(status_counts)
    artifact["rate_limited"] = rate_limited
    artifact["environment_snapshot"] = build_environment(str(Path(__file__).resolve().parents[2]))
    return artifact


def print_summary(artifact: dict):
    """Print the console summary from the artifact, never from raw counters."""
    meta = artifact["metadata"]
    latency = artifact["latency"]
    throughput = artifact["throughput"]
    errors = artifact["errors"]

    def fmt(value, unit=""):
        return "not measured" if value is None else f"{value}{unit}"

    print(f"\n{'=' * 60}")
    print(f"Performance artifact: {meta['status']}")
    if meta["blocked_reason"]:
        print(f"  blocked_reason: {meta['blocked_reason']}")
    print(f"  git_sha: {meta['git_sha'] or 'unavailable'} (dirty={meta['git_dirty']})")
    print(f"  requests: total={errors['total']} success={errors['success']} failure={errors['failure']}")
    print(
        f"  latency p50/p95/p99: {fmt(latency.get('p50'), 'ms')} / "
        f"{fmt(latency.get('p95'), 'ms')} / {fmt(latency.get('p99'), 'ms')}"
    )
    print(f"  throughput: {fmt(throughput.get('qps'), ' req/s')}")
    print(f"  per-endpoint: {artifact.get('endpoint_counts') or {}}")
    print(f"  429 responses: {artifact.get('rate_limited', 0)}")
    print(f"{'=' * 60}\n")


@events.quit.add_listener
def generate_report(environment, **kwargs):
    """Write the performance evidence artifact when the load test ends."""
    artifact = build_report(environment)
    print_summary(artifact)
    print(build_performance_report(artifact).split("\n", 2)[2])
