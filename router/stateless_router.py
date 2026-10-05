"""
无状态请求路由器（readme 4.2 节）

设计原则：
- 遵循 vLLM 原生 continuous batching 机制
- 不在外部实现任何请求队列或优先级抢占逻辑
- 仅做无状态路由分发，所有并发调度完全交由 vLLM 内部 Scheduler 处理

`route_chat` 是 canonical vLLM 生成路径，它的有界重试契约（失败分类、次数上限、
总 deadline、退避）全部来自 :mod:`router.vllm_resilience`。本模块只负责传输与
解析。契约要点：

- 端点在调用方解析一次后固定，所有 attempt 复用同一个 endpoint_key，因此重试
  **不可能**把 14B 请求悄悄降级到 4B。
- 每次 `route_chat` 调用向既有 collector 记录一次 request 级指标，每个 HTTP
  attempt 记录一次 attempt 级指标，成功路径与重试次数无关。
"""

from __future__ import annotations

import logging

import httpx

from common.config import get_config_dict
from router.vllm_resilience import (
    MONOTONIC_CLOCK,
    REAL_SLEEP,
    REQUEST_OUTCOME_BUDGET_EXHAUSTED,
    REQUEST_OUTCOME_FAILED,
    REQUEST_OUTCOME_SUCCESS,
    FailureClass,
    RetryPolicy,
    VLLMGenerationError,
    VLLMMetricsSink,
    backoff_delay_seconds,
    classify_http_status,
    classify_response_exception,
    classify_transport_exception,
    parse_retry_after_seconds,
)

config = get_config_dict()

logger = logging.getLogger(__name__)


class _CanonicalVLLMMetricsSink(VLLMMetricsSink):
    """Publishes the generation-path counters onto the canonical collector.

    ``monitoring/otel_tracer.py::MetricsCollector`` is the collector
    ``GET /api/metrics`` already serves, so this reuses the existing metric
    registry and introduces no second exposition path. No new registry, no
    sidecar, no new endpoint.

    The import is lazy and the whole body is defensive, matching
    ``api/middleware.py``: a metrics problem must never be the reason a request
    fails, and the generation path must stay importable without the API layer.
    """

    def record_attempt(self, *, succeeded: bool, failure_class: str | None) -> None:
        try:
            from api.routes import get_metrics

            get_metrics().record_vllm_generation_attempt(
                succeeded=succeeded,
                failure_class=failure_class,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("vLLM generation attempt metric recording failed: %s", exc)

    def record_request(self, *, outcome: str, retries: int, elapsed_ms: float) -> None:
        try:
            from api.routes import get_metrics

            get_metrics().record_vllm_generation_request(
                outcome=outcome,
                retries=retries,
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("vLLM generation request metric recording failed: %s", exc)


class StatelessRouter:
    """
    无状态轻量路由器

    路由规则：
    - Rewrite 请求 → vLLM-4B (GPU1, port 8101)
    - Gen 简单请求 → vLLM-4B (GPU1, port 8101) —— 同实例，不同 prompt
    - Gen 复杂请求 → vLLM-14B (GPU0, port 8100)

    关键约束：
    - 不进行请求排队
    - 不维护优先级队列
    - 不干预 vLLM 内部 continuous batching 决策
    - Rewrite 繁忙时降级为应用层结构兜底，绝不进入阻塞式等待队列
    """

    def __init__(
        self,
        *,
        policy: RetryPolicy | None = None,
        clock=MONOTONIC_CLOCK,
        sleep=REAL_SLEEP,
        metrics_sink: VLLMMetricsSink | None = None,
    ):
        # 端点 URL 从环境变量（Docker 部署）或 config.json（本地部署）读取
        import os as _os

        vllm_4b_port = config["gpu1"]["models"]["vllm_4b"]["port"]
        gen_14b_port = config["gpu0"]["models"]["gen_14b"]["port"]
        self.endpoints = {
            "gen_4b": _os.environ.get("VLLM_4B_URL", f"http://localhost:{vllm_4b_port}"),
            "gen_14b": _os.environ.get("VLLM_GEN_14B_URL", f"http://localhost:{gen_14b_port}"),
        }
        self.timeout_seconds = float(_os.environ.get("VLLM_TIMEOUT_SECONDS", "10.0"))
        self.retry_policy = policy or RetryPolicy.from_env(per_attempt_timeout_seconds=self.timeout_seconds)
        self._clock = clock
        self._sleep = sleep
        self._metrics = metrics_sink if metrics_sink is not None else _CanonicalVLLMMetricsSink()
        self._client = httpx.Client(timeout=self.timeout_seconds)
        logger.info(
            "StatelessRouter 初始化完成 (max_attempts=%d, total_deadline=%.1fs)",
            self.retry_policy.max_attempts,
            self.retry_policy.total_deadline_seconds,
        )

    def route_chat(
        self,
        endpoint_key: str,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> dict:
        """
        调用 vLLM OpenAI-compatible Chat Completions API

        有界重试契约（见 :mod:`router.vllm_resilience`）：单次调用的总 attempt 数（含首次）
        最多为 ``retry_policy.max_attempts``——默认 2（1 次额外 retry），代码硬上限
        ``HARD_MAX_ATTEMPTS`` 为 3（最多 2 次额外 retry），任何配置都不能超过 3。总耗时
        （含退避 sleep）不超过 ``retry_policy.total_deadline_seconds``。只有 allow-list 里的
        408/429/502/503/504 会重试；ordinary 4xx、500 与 malformed 响应不重试。端点在整次
        调用内固定，重试不会切换模型。

        Args:
            endpoint_key: "gen_4b" / "gen_14b"（Rewrite 和简单 Gen 共用 gen_4b 端点）
            messages: [{"role": "system/user/assistant", "content": str}]
            max_tokens: 最大输出 token 数
            temperature: 生成温度

        Returns:
            {"content": str, "prefix_cache_hit": bool | None}
            - content: 生成的文本内容
            - prefix_cache_hit: vLLM Prefix Cache 命中状态（从响应头提取）

        Raises:
            VLLMGenerationError: 所有传输、超时、HTTP 状态与响应解析失败都归一化为
                同一个 sanitised 异常类型，携带 ``failure_class`` / ``status_code`` /
                ``attempts``，不携带 endpoint URL、凭据或响应体。
        """
        base_url = self.endpoints.get(endpoint_key, "")
        if not base_url:
            error = VLLMGenerationError(
                FailureClass.CONFIGURATION,
                endpoint_key,
                attempts=0,
            )
            self._record_request(REQUEST_OUTCOME_FAILED, retries=0, started_at=self._clock())
            raise error

        url = f"{base_url}/v1/chat/completions"
        payload = {
            "model": "default",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        policy = self.retry_policy
        started_at = self._clock()
        attempts = 0
        retries = 0

        while True:
            remaining = policy.total_deadline_seconds - (self._clock() - started_at)
            # Deadline 先于 attempt 判定：预算已用尽时不再发起新的 HTTP 请求。
            # `RetryPolicy` 已保证两个分量均为正，因此这里不需要再校验 timeout > 0。
            if remaining <= 0:
                raise self._budget_exhausted(endpoint_key, attempts, retries, started_at) from None

            attempts += 1
            # 单次 attempt 的 timeout 同时受 per-attempt 上限与剩余预算约束，
            # 调大 per-attempt 超时无法让请求突破 deadline。
            try:
                result = self._post_chat_once(
                    endpoint_key=endpoint_key,
                    url=url,
                    payload=payload,
                    timeout=min(policy.per_attempt_timeout_seconds, remaining),
                    attempts=attempts,
                    started_at=started_at,
                )
            except VLLMGenerationError as error:
                self._record_attempt(
                    succeeded=False,
                    failure_class=error.failure_class.value,
                )
                delay = None
                if error.retryable and attempts < policy.max_attempts:
                    delay = backoff_delay_seconds(
                        policy,
                        attempts_made=attempts,
                        retry_after_seconds=error.retry_after_seconds,
                    )
                if delay is not None:
                    remaining_after = policy.total_deadline_seconds - (self._clock() - started_at)
                    if delay < remaining_after:
                        retries += 1
                        logger.warning(
                            "vLLM generation attempt %d/%d failed (class=%s, status=%s); retrying in %.2fs",
                            attempts,
                            policy.max_attempts,
                            error.failure_class.value,
                            error.status_code,
                            delay,
                        )
                        self._sleep(delay)
                        continue
                    # 装不下这次退避就不再重试，而不是压缩退避或拉长 deadline ——
                    # 那正是 retry storm 的来源。它是本进程的预算问题，因此按预算耗尽上抛。
                    raise self._budget_exhausted(
                        endpoint_key,
                        attempts,
                        retries,
                        started_at,
                        status_code=error.status_code,
                    ) from None
                # 三种终态在这里汇合：失败类不可重试、次数上限已到、或服务端要求的等待
                # 长于本策略的退避上限（`delay is None`）。三者的诚实终态都是对端的
                # 失败本身，不是预算耗尽。
                self._record_request(REQUEST_OUTCOME_FAILED, retries=retries, started_at=started_at)
                raise error from None

            self._record_attempt(succeeded=True, failure_class=None)
            self._record_request(REQUEST_OUTCOME_SUCCESS, retries=retries, started_at=started_at)
            return result

    def _budget_exhausted(
        self,
        endpoint_key: str,
        attempts: int,
        retries: int,
        started_at: float,
        *,
        status_code: int | None = None,
    ) -> VLLMGenerationError:
        """Build the terminal budget error and record its request metric once.

        Owning the single record here is what keeps ``route_chat`` from emitting
        two request-level outcomes for one call: the retry branch delegates to
        this method instead of recording on its way out.
        """
        elapsed = self._clock() - started_at
        self._record_request(
            REQUEST_OUTCOME_BUDGET_EXHAUSTED,
            retries=retries,
            started_at=started_at,
        )
        return VLLMGenerationError(
            FailureClass.BUDGET_EXHAUSTED,
            endpoint_key,
            attempts=attempts,
            status_code=status_code,
            elapsed_seconds=elapsed,
        )

    def _record_attempt(self, *, succeeded: bool, failure_class: str | None) -> None:
        """Publish one attempt outcome. A metrics failure must not fail the request."""
        try:
            self._metrics.record_attempt(succeeded=succeeded, failure_class=failure_class)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("vLLM generation attempt metric recording failed: %s", exc)

    def _record_request(self, outcome: str, *, retries: int, started_at: float) -> None:
        elapsed_ms = max((self._clock() - started_at) * 1000.0, 0.0)
        try:
            self._metrics.record_request(outcome=outcome, retries=retries, elapsed_ms=elapsed_ms)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("vLLM generation request metric recording failed: %s", exc)

    def _post_chat_once(
        self,
        *,
        endpoint_key: str,
        url: str,
        payload: dict,
        timeout: float,
        attempts: int,
        started_at: float,
    ) -> dict:
        """One HTTP attempt. Never leaks a raw transport exception to the caller."""
        try:
            resp = self._client.post(url, json=payload, timeout=timeout)
        except Exception as exc:
            failure_class = classify_transport_exception(exc)
            # 原始异常只进 DEBUG 日志（本地排障用），不进异常链，也不进用户可见消息：
            # httpx 的 str() 包含完整 URL，URL 可能带凭据。
            logger.debug("vLLM endpoint %s transport error: %s", endpoint_key, exc, exc_info=True)
            raise VLLMGenerationError(
                failure_class,
                endpoint_key,
                attempts=attempts,
                elapsed_seconds=self._clock() - started_at,
            ) from None

        # 显式判定状态码而不是 raise_for_status()：错误分支需要拿到 status 来分类。
        if not 200 <= resp.status_code < 300:
            failure_class = classify_http_status(resp.status_code)
            if failure_class is FailureClass.HTTP_PERMANENT:
                logger.error(
                    "vLLM 端点拒绝请求: %s (status=%s)",
                    endpoint_key,
                    resp.status_code,
                )
            raise VLLMGenerationError(
                failure_class,
                endpoint_key,
                attempts=attempts,
                status_code=resp.status_code,
                elapsed_seconds=self._clock() - started_at,
                retry_after_seconds=parse_retry_after_seconds(resp.headers),
            ) from None

        # 提取 vLLM Prefix Cache 命中信息（响应头）
        # vLLM 在启用 prefix caching 时会返回 x-prefix-cache-hit 头
        prefix_cache_header = resp.headers.get("x-prefix-cache-hit")
        prefix_cache_hit = None
        if prefix_cache_header is not None:
            prefix_cache_hit = prefix_cache_header.lower() in ("true", "1", "yes")

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("completion content is not a string")
        except Exception as exc:
            failure_class = classify_response_exception(exc)
            # 响应体可能回显请求或携带内部信息，因此不进错误消息，只记分类。
            logger.error("vLLM 响应解析异常: %s (class=%s)", endpoint_key, failure_class.value)
            raise VLLMGenerationError(
                failure_class,
                endpoint_key,
                attempts=attempts,
                status_code=resp.status_code,
                elapsed_seconds=self._clock() - started_at,
            ) from None

        return {
            "content": content,
            "prefix_cache_hit": prefix_cache_hit,
        }

    def route_completion(
        self,
        endpoint_key: str,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        """
        调用 vLLM Completions API（兼容旧接口）

        范围边界：这条 rewrite 路径**不在**本次 resilience 契约内。它保持单次
        attempt、不分类、不重试，异常类型也仍是原始 httpx 异常。把它一并改造会
        改变 rewrite 的重试语义与错误面，属于另一次改动；这里显式记录该缺口，
        以免读者误以为整个 vLLM 客户端都已加固。

        Returns:
            生成的文本内容
        """
        base_url = self.endpoints.get(endpoint_key, "")
        if not base_url:
            raise ValueError(f"未知端点: {endpoint_key}")

        url = f"{base_url}/v1/completions"
        payload = {
            "model": "default",
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        try:
            resp = self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["text"]
        except httpx.TimeoutException:
            logger.error(f"vLLM 端点超时: {endpoint_key}")
            raise
        except httpx.ConnectError:
            logger.error(f"vLLM 端点连接失败: {endpoint_key}")
            raise

    def get_health(self, endpoint_key: str) -> bool:
        """检查端点健康状态"""
        try:
            base_url = self.endpoints.get(endpoint_key, "")
            resp = self._client.get(f"{base_url}/health", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

    def check_any_endpoint_alive(self) -> bool:
        """检查是否有任意端点存活"""
        for key in self.endpoints:
            if self.get_health(key):
                return True
        return False
