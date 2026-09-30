#!/usr/bin/env python3
"""
基准测试运行器 — 化妆品 RAG 系统。

用法:
    # 运行默认基准测试（2 用户，持续 60 秒）
    python tests/load/run_benchmark.py

    # 指定参数
    python tests/load/run_benchmark.py --users 5 --spawn-rate 1 --run-time 120s

    # 指定 API 地址和 Token
    python tests/load/run_benchmark.py --host http://localhost:8000 --token your_token

    # 仅生成报告（从已有 JSON 结果生成 HTML）
    python tests/load/run_benchmark.py --report-only reports/benchmark/benchmark_20260101_120000.json

    # 对比两次运行
    python tests/load/run_benchmark.py --compare reports/benchmark/run1.json reports/benchmark/run2.json
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parent.parent.parent / "reports" / "benchmark"
LOCUSTFILE = Path(__file__).resolve().parent / "locustfile.py"


def run_benchmark(host: str, users: int, spawn_rate: int, run_time: str, token: str = "") -> dict | None:
    """运行 Locust 压测并返回结果。"""
    print(f"\n{'=' * 60}")
    print("🚀 开始压测")
    print(f"   目标: {host}")
    print(f"   并发用户: {users}")
    print(f"   启动速率: {spawn_rate}/秒")
    print(f"   持续时间: {run_time}")
    print(f"{'=' * 60}\n")

    env = {
        "BENCHMARK_API_BASE": host,
        "BENCHMARK_AUTH_TOKEN": token,
        "LOCUST_HOST": host,
    }

    cmd = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        str(LOCUSTFILE),
        "--headless",
        "-u",
        str(users),
        "-r",
        str(spawn_rate),
        "-t",
        run_time,
        "--host",
        host,
        "--print-stats",
        "--stop-timeout",
        "10",
    ]

    result = subprocess.run(cmd, env={**__import__("os").environ, **env}, cwd=str(REPORT_DIR.parent.parent.parent))

    if result.returncode != 0:
        print(f"❌ Locust 退出码: {result.returncode}")
        return None

    # 找到最新生成的报告
    report_files = sorted(REPORT_DIR.glob("benchmark_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if report_files:
        latest = report_files[0]
        with open(latest, encoding="utf-8") as f:
            return json.load(f)
    return None


def generate_html_report(data: dict) -> str:
    """从基准测试数据生成 HTML 报告。"""
    bm = data.get("benchmark", {})
    lat = data.get("latency_ms", {})
    cache = data.get("cache", {})
    err = data.get("errors", {})
    sys_stats = data.get("system_stats", {})
    sys_cache = sys_stats.get("cache_hit_rate", {})

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RAG 系统基准测试报告</title>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', sans-serif; max-width: 960px; margin: 0 auto; padding: 2rem; background: #f8f9fa; color: #333; }}
  h1 {{ color: #1a1a2e; border-bottom: 3px solid #4361ee; padding-bottom: 0.5rem; }}
  h2 {{ color: #2d3436; margin-top: 2rem; }}
  .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }}
  .card {{ background: #fff; border-radius: 12px; padding: 1.2rem; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  .card .label {{ font-size: 0.85rem; color: #636e72; margin-bottom: 0.3rem; }}
  .card .value {{ font-size: 1.6rem; font-weight: 700; color: #2d3436; }}
  .card .value.green {{ color: #00b894; }}
  .card .value.blue {{ color: #0984e3; }}
  .card .value.orange {{ color: #e17055; }}
  .card .value.red {{ color: #d63031; }}
  table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
  th, td {{ padding: 0.75rem 1rem; text-align: left; }}
  th {{ background: #4361ee; color: #fff; font-weight: 600; }}
  tr:nth-child(even) {{ background: #f1f2f6; }}
  .timestamp {{ color: #636e72; font-size: 0.9rem; margin-top: 0.5rem; }}
  .badge {{ display: inline-block; padding: 0.2rem 0.6rem; border-radius: 4px; font-size: 0.8rem; font-weight: 600; }}
  .badge.ok {{ background: #00b89420; color: #00b894; }}
  .badge.warn {{ background: #e1705520; color: #e17055; }}
  .badge.fail {{ background: #d6303120; color: #d63031; }}
</style>
</head>
<body>
<h1>📊 RAG 系统基准测试报告</h1>
<p class="timestamp">生成时间: {bm.get("timestamp", "N/A")} | 总请求数: {lat.get("count", 0)}</p>

<h2>📋 测试配置</h2>
<table>
  <tr><th>参数</th><th>值</th></tr>
  <tr><td>并发用户数</td><td>{bm.get("concurrent_users", "N/A")}</td></tr>
  <tr><td>测试时长</td><td>{bm.get("duration_seconds", 0):.1f} 秒</td></tr>
  <tr><td>总请求数</td><td>{bm.get("total_requests", 0)}</td></tr>
</table>

<h2>⏱ 延迟分布</h2>
<div class="summary">
  <div class="card">
    <div class="label">平均延迟</div>
    <div class="value blue">{lat.get("avg", 0):.1f} <small>ms</small></div>
  </div>
  <div class="card">
    <div class="label">P50（中位数）</div>
    <div class="value green">{lat.get("p50", 0):.1f} <small>ms</small></div>
  </div>
  <div class="card">
    <div class="label">P95</div>
    <div class="value orange">{lat.get("p95", 0):.1f} <small>ms</small></div>
  </div>
  <div class="card">
    <div class="label">P99</div>
    <div class="value red">{lat.get("p99", 0):.1f} <small>ms</small></div>
  </div>
</div>
<div class="summary" style="margin-top: 0.5rem;">
  <div class="card">
    <div class="label">最低延迟</div>
    <div class="value green">{lat.get("min", 0):.1f} <small>ms</small></div>
  </div>
  <div class="card">
    <div class="label">最高延迟</div>
    <div class="value red">{lat.get("max", 0):.1f} <small>ms</small></div>
  </div>
</div>

<h2>💾 缓存命中率</h2>
<div class="summary">
  <div class="card">
    <div class="label">客户端缓存命中率</div>
    <div class="value blue">{cache.get("client_cache_hit_rate_pct", 0):.1f}%</div>
  </div>
  <div class="card">
    <div class="label">L1 命中率（系统级）</div>
    <div class="value green">{sys_cache.get("L1", 0) * 100:.1f}%</div>
  </div>
  <div class="card">
    <div class="label">L2 命中率（系统级）</div>
    <div class="value orange">{sys_cache.get("L2", 0) * 100:.1f}%</div>
  </div>
</div>
<div class="summary" style="margin-top: 0.5rem;">
  <div class="card">
    <div class="label">缓存命中次数</div>
    <div class="value">{cache.get("client_cache_hits", 0)}</div>
  </div>
  <div class="card">
    <div class="label">缓存未命中次数</div>
    <div class="value">{cache.get("client_cache_misses", 0)}</div>
  </div>
</div>

<h2>⚠️ 错误统计</h2>
<div class="summary">
  <div class="card">
    <div class="label">总错误数</div>
    <div class="value red">{err.get("total", 0)}</div>
  </div>
  <div class="card">
    <div class="label">错误率</div>
    <div class="value {"green" if err.get("error_rate_pct", 0) < 1 else "orange" if err.get("error_rate_pct", 0) < 5 else "red"}">{err.get("error_rate_pct", 0):.2f}%</div>
  </div>
</div>

<h2>📈 结论</h2>
<ul>
<li><strong>延迟:</strong> P50={lat.get("p50", 0):.1f}ms，P99={lat.get("p99", 0):.1f}ms</li>
<li><strong>缓存效率:</strong> 客户端命中率 {cache.get("client_cache_hit_rate_pct", 0):.1f}%</li>
<li><strong>系统稳定性:</strong> 错误率 {err.get("error_rate_pct", 0):.2f}%</li>
</ul>
</body>
</html>"""
    return html


def save_html_report(data: dict, report_path: Path) -> Path:
    """生成并保存 HTML 报告。"""
    html = generate_html_report(data)
    html_path = report_path.with_suffix(".html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"   HTML 报告: {html_path}")
    return html_path


def compare_runs(path1: str, path2: str):
    """对比两次基准测试运行结果。"""
    with open(path1, encoding="utf-8") as f:
        data1 = json.load(f)
    with open(path2, encoding="utf-8") as f:
        data2 = json.load(f)

    lat1 = data1.get("latency_ms", {})
    lat2 = data2.get("latency_ms", {})
    cache1 = data1.get("cache", {})
    cache2 = data2.get("cache", {})

    print(f"\n{'=' * 60}")
    print("📊 基准测试对比")
    print(f"{'=' * 60}")
    print(f"{'指标':<30} {'运行 1':<15} {'运行 2':<15} {'变化':<15}")
    print(f"{'-' * 75}")
    for key in ["avg", "p50", "p95", "p99"]:
        v1 = lat1.get(key, 0)
        v2 = lat2.get(key, 0)
        change = f"{((v2 - v1) / max(v1, 0.001) * 100):+.1f}%" if v1 else "N/A"
        print(f"{f'延迟 {key} (ms)':<30} {v1:<15.1f} {v2:<15.1f} {change:<15}")
    hr1 = cache1.get("client_cache_hit_rate_pct", 0)
    hr2 = cache2.get("client_cache_hit_rate_pct", 0)
    hr_change = f"{hr2 - hr1:+.1f}pct"
    print(f"{'缓存命中率 (%)':<30} {hr1:<15.1f} {hr2:<15.1f} {hr_change:<15}")
    print(f"{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(description="RAG 系统基准测试运行器")
    parser.add_argument("--host", default="http://localhost:8000", help="API 地址")
    parser.add_argument("--token", default="", help="认证 Token")
    parser.add_argument("--users", type=int, default=3, help="并发用户数")
    parser.add_argument("--spawn-rate", type=int, default=1, help="启动速率 (用户/秒)")
    parser.add_argument("--run-time", default="60s", help="持续时长 (例如 60s, 5m)")
    parser.add_argument("--report-only", help="仅从已有 JSON 生成 HTML 报告")
    parser.add_argument("--compare", nargs=2, metavar=("JSON1", "JSON2"), help="对比两次运行")

    args = parser.parse_args()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    if args.compare:
        compare_runs(args.compare[0], args.compare[1])
        return

    if args.report_only:
        report_path = Path(args.report_only)
        if not report_path.exists():
            print(f"❌ 文件不存在: {report_path}")
            sys.exit(1)
        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)
        save_html_report(data, report_path)
        print("✅ HTML 报告已生成")
        return

    # 运行压测
    result = run_benchmark(args.host, args.users, args.spawn_rate, args.run_time, args.token)
    if result:
        report_path = REPORT_DIR / f"benchmark_{time.strftime('%Y%m%d_%H%M%S')}"
        with open(report_path.with_suffix(".json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        html_path = save_html_report(result, report_path.with_suffix(".json"))
        print("\n✅ 基准测试完成")
        print(f"   JSON 报告: {report_path}.json")
        print(f"   HTML 报告: {html_path}")
    else:
        print("❌ 基准测试失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
