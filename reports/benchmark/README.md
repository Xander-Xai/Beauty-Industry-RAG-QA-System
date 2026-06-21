# Benchmark Reports

## 目录说明

`reports/benchmark/` 目录存放 Locust 压测产生的 JSON 格式报告。

## 报告文件命名

```
benchmark_YYYYMMDD_HHMMSS.json
```

## 报告字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `benchmark.timestamp` | string | 压测结束时间 |
| `benchmark.duration_seconds` | float | 压测持续时长 |
| `benchmark.total_requests` | int | 总请求数 |
| `benchmark.concurrent_users` | int | 并发用户数 |
| `latency_ms.avg` | float | 平均延迟 (ms) |
| `latency_ms.p50` | float | 中位延迟 (ms) |
| `latency_ms.p95` | float | P95 延迟 (ms) |
| `latency_ms.p99` | float | P99 延迟 (ms) |
| `cache.client_cache_hit_rate_pct` | float | 客户端统计缓存命中率 (%) |
| `system_stats.cache_hit_rate` | object | 系统级 L1/L2/L2_SESSION 命中率 |
| `system_stats.prefix_cache_hit_rate` | float | Prefix Cache 命中率 |
| `errors.total` | int | 错误请求数 |
| `errors.error_rate_pct` | float | 错误率 (%) |

## 运行方式

```bash
# 安装依赖
pip install -r requirements-loadtest.txt

# 运行压测 (单机模式, 10 并发, 2/s 加速, 5 分钟)
cd tests/load
locust --headless -u 10 -r 2 --run-time 5m --host http://localhost:8000

# 运行压测 (Web UI 模式)
locust -u 10 -r 2 --host http://localhost:8000
```

## 分析

可编写脚本遍历 `benchmark_*.json` 文件，提取各次压测的 P50/P95/P99 延迟趋势和缓存命中率变化。
