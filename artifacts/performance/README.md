# Performance evidence artifacts

Runtime output of the load/performance harness lives here:

```text
artifacts/performance/<run-id>/
├── metadata.json           # status, git provenance, host, workload, counts, limitations
├── environment.json        # dependency / runtime versions
├── workload.json           # declared workload, verbatim
├── latency_metrics.json    # p50/p95/p99 + count
├── throughput_metrics.json # qps and whether it was measured
├── errors.json             # success/failure counts, status, blocked_reason
├── report.md               # human-readable summary
├── raw_locust_stats.csv    # optional passthrough
└── raw_failures.csv        # optional passthrough
```

## The one rule

**Not executed is not zero.**

A run that never reached the service must never emit `p95 = 0.0` or `qps = 0.0`,
because those read as *fast and healthy*. Every unmeasured quantity is `null`, and
the latency block is reduced to `{"count": 0}` with no latency key at all.

`tests/load/locustfile.py::LatencyStats` follows the same rule: its percentile
properties return `None`, not `0.0`, when no sample was collected.

## Status semantics

| Status | Meaning |
|---|---|
| `EXECUTED` | The full declared workload ran and every request completed. |
| `PARTIAL` | The harness ran but some requests failed, or the declared concurrency was not reached. |
| `BLOCKED` | The workload never ran. `blocked_reason` says why. |

Status is **derived from what happened**, not chosen by the caller:

- `blocked_reason` present → `BLOCKED`, regardless of any other signal.
- zero requests → `BLOCKED` even without an explicit reason.
- any failure → `PARTIAL`.
- observed concurrency below the declared value → `PARTIAL`, because the
  declared workload did not complete even if every request succeeded.

A `BLOCKED` run must carry a `blocked_reason`, and a non-`BLOCKED` run must not
carry one. Both directions are enforced when the artifact is built.

## What blocks a run

`tests/load/locustfile.py` runs a preflight before load:

- API unreachable → `api_unreachable`
- `/api/health` non-200 → `api_health_status_<code>`
- **no bearer token** → `missing_auth_token`

The token case matters. Every `/api/*` endpoint except `/api/health` requires
authentication, so a tokenless run would measure a stream of `401`s and report
them as a fast, healthy service. Blocking is the honest outcome.

## Reading the output honestly

* A `BLOCKED` artifact contains **no** performance result. `latency_metrics.json`
  and `throughput_metrics.json` record the absence instead.
* Load-test utilities existing is not a performance result. No artifact is
  committed, so this repository claims **no** measured QPS, P95 or P99.
* These are repository measurements on one host. They are **not** production SLO
  results and they do not reproduce historical production traffic.
* PRD latency/QPS figures remain `DESIGN_TARGET` unless an artifact recorded
  under the *same* workload says otherwise.

## Version control

Generated runs are intentionally not committed; `.gitignore` excludes
`artifacts/performance/*` while keeping this README. To make a run durable
evidence, promote the specific run directory deliberately and record `git_sha`,
`git_dirty` and `system_version` alongside it.

## Commands

```bash
# what can actually be measured in this environment (preflight only)
python -m benchmarks.performance --probe --host http://localhost:8000

# attempt a real run; writes a BLOCKED artifact when prerequisites are missing
BENCHMARK_AUTH_TOKEN=<token> python tests/load/run_benchmark.py \
  --host http://localhost:8000 --users 3 --run-time 60s
```