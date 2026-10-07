# Benchmark Reports

> **This directory is not the performance evidence artifact.**
> The canonical, guarded performance contract is the seven-file artifact under
> [`artifacts/performance/<run-id>/`](../../artifacts/performance/README.md), written by
> `tests/load/locustfile.py` through `benchmarks/performance.py::write_artifact`.
> Nothing in this directory is a `REPO_VERIFIED` result, and no repository claim depends on it.

## What this directory holds

Plain-Locust HTML/JSON reports produced by the standalone wrapper
`tests/load/run_benchmark.py`. It runs `locust` directly and summarises whatever it finds in
`REPORT_DIR` (`tests/load/locustfile.py` / `run_benchmark.py` both resolve it to
`reports/benchmark/`).

Use `python -m benchmarks.performance` when you want an artifact that can be cited as
evidence — it is the path that records provenance, derives `EXECUTED`/`PARTIAL`/`BLOCKED`
from what actually ran, and writes `null` rather than `0` for anything unmeasured.

## Report file naming

```
benchmark_YYYYMMDD_HHMMSS.json
```

## Report field schema

Read-only, by `tests/load/run_benchmark.py`. This is the wrapper's own summary shape and is
independent of the seven-file artifact contract.

| Field | Type | Meaning |
|------|------|---------|
| `benchmark.timestamp` | string | run end time |
| `benchmark.duration_seconds` | float | run duration |
| `benchmark.total_requests` | int | total requests |
| `benchmark.concurrent_users` | int | concurrent users |
| `latency_ms.count` | int | latency sample count |
| `latency_ms.avg` | float | mean latency (ms) |
| `latency_ms.min` / `latency_ms.max` | float | latency bounds (ms) |
| `latency_ms.p50` | float | median latency (ms) |
| `latency_ms.p95` | float | P95 latency (ms) |
| `latency_ms.p99` | float | P99 latency (ms) |
| `cache.client_cache_hits` | int | client-side cache hits |
| `cache.client_cache_misses` | int | client-side cache misses |
| `cache.client_cache_hit_rate_pct` | float | client-side cache hit rate (%) |
| `system_stats.cache_hit_rate` | object | server-side per-level hit rate (`L1`, `L2`, `L2_SESSION`) |
| `errors.total` | int | failed requests |
| `errors.error_rate_pct` | float | error rate (%) |

## Running the wrapper

```bash
pip install -r requirements-loadtest.txt

# headless, single host
cd tests/load
locust --headless -u 10 -r 2 --run-time 5m --host http://localhost:8000

# or via the wrapper, which also renders an HTML summary
python tests/load/run_benchmark.py --users 10 --spawn-rate 2 --run-time 5m \
  --host http://localhost:8000 --token "$SERVICE_AUTH_TOKEN"
```

A run against an unreachable API or a missing bearer token records a `BLOCKED` reason in the
canonical artifact rather than emitting a number. That path is `PENDING` until a real run is
committed; see [`docs/deferred-runtime-validation.md`](../../docs/deferred-runtime-validation.md)
(`VAL-PERF-001`) and [issue #32](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/32).