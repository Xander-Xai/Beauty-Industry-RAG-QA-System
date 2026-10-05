"""Bounded vLLM generation resilience contract.

Every test here runs entirely in-process: the vLLM endpoint is an
``httpx.MockTransport`` handler and the clock is a manual clock, so there is no
socket, no GPU, no vLLM server and no wall-clock race. A test that passes here
proves the *contract* — classification, attempt cap, deadline, determinism,
sanitised errors, exactly-once metrics — and never proves anything about real
model quality, real latency or production behaviour.
"""

from __future__ import annotations

import os
import sys
import types

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DEPLOYMENT_MODE"] = "development"

# torch mock shim — same defensive pattern as the other suite modules.
try:
    import torch  # noqa: F401
except ImportError:
    _fake_torch = types.ModuleType("torch")
    _fake_cuda = types.ModuleType("torch.cuda")
    _fake_cuda.is_available = lambda: False
    _fake_cuda.OutOfMemoryError = type("OutOfMemoryError", (Exception,), {})
    _fake_torch.cuda = _fake_cuda
    sys.modules["torch"] = _fake_torch
    sys.modules["torch.cuda"] = _fake_cuda

from router.stateless_router import StatelessRouter  # noqa: E402
from router.vllm_resilience import (  # noqa: E402
    HARD_MAX_ATTEMPTS,
    REQUEST_OUTCOME_BUDGET_EXHAUSTED,
    REQUEST_OUTCOME_FAILED,
    REQUEST_OUTCOME_SUCCESS,
    TRANSIENT_HTTP_STATUSES,
    FailureClass,
    ManualClock,
    RetryPolicy,
    VLLMGenerationError,
    backoff_delay_seconds,
    classify_http_status,
    classify_response_exception,
    classify_transport_exception,
    parse_retry_after_seconds,
    sanitize_endpoint_key,
)

MESSAGES = [{"role": "user", "content": "烟酰胺推荐浓度是多少？"}]


class RecordingSink:
    """Captures every metrics call so counts can be asserted exactly."""

    def __init__(self) -> None:
        self.attempts: list[tuple[bool, str | None]] = []
        self.requests: list[dict] = []

    def record_attempt(self, *, succeeded: bool, failure_class: str | None) -> None:
        self.attempts.append((succeeded, failure_class))

    def record_request(self, *, outcome: str, retries: int, elapsed_ms: float) -> None:
        self.requests.append({"outcome": outcome, "retries": retries, "elapsed_ms": elapsed_ms})

    @property
    def failures(self) -> list[str]:
        return [failure_class for succeeded, failure_class in self.attempts if not succeeded]

    @property
    def outcomes(self) -> list[str]:
        return [request["outcome"] for request in self.requests]


class FakeVLLM:
    """An in-process vLLM endpoint driven by a scripted handler.

    The handler receives the real ``httpx.Request`` and returns a real
    ``httpx.Response``, so URL construction, headers, JSON decoding and
    exception propagation all run through genuine httpx code. Only the socket is
    replaced.
    """

    def __init__(self, clock: ManualClock, script: list) -> None:
        self.clock = clock
        self.script = list(script)
        self.requests: list[httpx.Request] = []
        #: endpoint key is not part of an httpx.Request, so the target base URL is
        #: recorded to prove retries never hop to a different endpoint.
        self.base_urls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        base = str(request.url).rsplit("/v1/", 1)[0]
        self.base_urls.append(base)
        index = len(self.requests) - 1
        if index >= len(self.script):
            raise AssertionError(f"unexpected extra request #{index + 1} to {base}")
        step = self.script[index]
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(request)
        return step

    @property
    def call_count(self) -> int:
        return len(self.requests)


def ok(content="烟酰胺推荐浓度为 2-5%。", headers=None):
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
        headers=headers or {},
    )


def build_router(clock: ManualClock, script: list, *, policy: RetryPolicy | None = None) -> tuple:
    fake = FakeVLLM(clock, script)
    sink = RecordingSink()
    router = StatelessRouter(
        policy=policy or RetryPolicy(),
        clock=clock,
        sleep=clock.sleep,
        metrics_sink=sink,
    )
    router.endpoints = {
        "gen_4b": "http://vllm-4b.internal:8101",
        "gen_14b": "http://vllm-14b.internal:8100",
    }
    # Replace the socket with the in-process transport.
    router._client.close()
    router._client = httpx.Client(
        transport=httpx.MockTransport(fake.handler),
        timeout=RetryPolicy().per_attempt_timeout_seconds,
    )
    return router, fake, sink


# ── 1. success on the first attempt ────────────────────────────────────────


class TestSuccessFirstAttempt:
    def test_first_attempt_success_issues_exactly_one_request(self):
        clock = ManualClock()
        router, fake, sink = build_router(clock, [ok()])

        result = router.route_chat("gen_4b", messages=MESSAGES)

        assert result["content"] == "烟酰胺推荐浓度为 2-5%。"
        assert fake.call_count == 1
        assert clock.sleeps == []

    def test_success_response_shape_is_unchanged(self):
        """The success contract is byte-identical to the pre-contract return value."""
        clock = ManualClock()
        router, _fake, _sink = build_router(
            clock,
            [ok(headers={"x-prefix-cache-hit": "true"})],
        )

        assert router.route_chat("gen_4b", messages=MESSAGES) == {
            "content": "烟酰胺推荐浓度为 2-5%。",
            "prefix_cache_hit": True,
        }

    @pytest.mark.parametrize(
        ("header", "expected"),
        [("true", True), ("1", True), ("yes", True), ("false", False), ("0", False)],
    )
    def test_prefix_cache_header_parsing_is_preserved(self, header, expected):
        clock = ManualClock()
        router, _fake, _sink = build_router(clock, [ok(headers={"x-prefix-cache-hit": header})])

        assert router.route_chat("gen_4b", messages=MESSAGES)["prefix_cache_hit"] is expected

    def test_absent_prefix_cache_header_stays_none(self):
        clock = ManualClock()
        router, _fake, _sink = build_router(clock, [ok()])

        assert router.route_chat("gen_4b", messages=MESSAGES)["prefix_cache_hit"] is None

    def test_request_payload_is_unchanged(self):
        clock = ManualClock()
        router, fake, _sink = build_router(clock, [ok()])

        router.route_chat("gen_4b", messages=MESSAGES, max_tokens=256, temperature=0.1)

        payload = __import__("json").loads(fake.requests[0].content)
        assert payload == {
            "model": "default",
            "messages": MESSAGES,
            "max_tokens": 256,
            "temperature": 0.1,
        }
        assert str(fake.requests[0].url) == "http://vllm-4b.internal:8101/v1/chat/completions"

    def test_success_records_one_attempt_and_one_request(self):
        clock = ManualClock()
        router, _fake, sink = build_router(clock, [ok()])

        router.route_chat("gen_4b", messages=MESSAGES)

        assert sink.attempts == [(True, None)]
        assert sink.outcomes == [REQUEST_OUTCOME_SUCCESS]
        assert sink.requests[0]["retries"] == 0


# ── 2. transient failure, then retry, then success ─────────────────────────


class TestTransientThenSuccess:
    @pytest.mark.parametrize("status", sorted(TRANSIENT_HTTP_STATUSES))
    def test_every_transient_status_recovers_on_the_retry(self, status):
        clock = ManualClock()
        router, fake, sink = build_router(clock, [httpx.Response(status), ok()])

        result = router.route_chat("gen_14b", messages=MESSAGES)

        assert result["content"] == "烟酰胺推荐浓度为 2-5%。"
        assert fake.call_count == 2
        assert sink.attempts == [(False, FailureClass.HTTP_TRANSIENT.value), (True, None)]
        assert sink.outcomes == [REQUEST_OUTCOME_SUCCESS]
        assert sink.requests[0]["retries"] == 1

    def test_timeout_recovers_on_the_retry(self):
        clock = ManualClock()
        router, fake, sink = build_router(
            clock,
            [httpx.ReadTimeout("read timed out"), ok()],
        )

        assert router.route_chat("gen_4b", messages=MESSAGES)["content"]
        assert fake.call_count == 2
        assert sink.failures == [FailureClass.TIMEOUT.value]

    def test_connection_failure_recovers_on_the_retry(self):
        clock = ManualClock()
        router, fake, sink = build_router(
            clock,
            [httpx.ConnectError("connection refused"), ok()],
        )

        assert router.route_chat("gen_4b", messages=MESSAGES)["content"]
        assert fake.call_count == 2
        assert sink.failures == [FailureClass.CONNECTION.value]

    def test_remote_protocol_error_is_a_connection_failure(self):
        clock = ManualClock()
        router, _fake, sink = build_router(
            clock,
            [httpx.RemoteProtocolError("server disconnected without response"), ok()],
        )

        router.route_chat("gen_4b", messages=MESSAGES)

        assert sink.failures == [FailureClass.CONNECTION.value]

    def test_backoff_schedule_is_deterministic(self):
        """The same failure sequence must produce the same sleep schedule."""
        schedules = []
        for _ in range(3):
            clock = ManualClock()
            policy = RetryPolicy(max_attempts=3, base_delay_seconds=0.25, max_delay_seconds=10.0)
            router, fake, _sink = build_router(
                clock,
                [httpx.Response(503), httpx.Response(503), ok()],
                policy=policy,
            )
            router.route_chat("gen_4b", messages=MESSAGES)
            schedules.append(clock.sleeps)
            assert fake.call_count == 3

        assert schedules[0] == schedules[1] == schedules[2]
        assert schedules[0] == [0.25, 0.5]

    def test_backoff_grows_exponentially_and_is_capped(self):
        policy = RetryPolicy(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=1.5)
        delays = [backoff_delay_seconds(policy, attempts_made=n) for n in (1, 2, 3, 4)]

        assert delays == [1.0, 1.5, 1.5, 1.5]

    def test_retry_after_raises_a_shorter_backoff(self):
        policy = RetryPolicy(base_delay_seconds=0.2, max_delay_seconds=2.0)

        assert backoff_delay_seconds(policy, attempts_made=1, retry_after_seconds=1.0) == 1.0

    def test_retry_after_longer_than_the_policy_forbids_the_retry(self):
        """Retrying sooner than the server asked is the behaviour that causes a storm."""
        policy = RetryPolicy(base_delay_seconds=0.2, max_delay_seconds=2.0)

        assert backoff_delay_seconds(policy, attempts_made=1, retry_after_seconds=30.0) is None

    def test_retry_after_longer_than_the_policy_stops_the_retry(self):
        """Honouring Retry-After: 30 means not retrying inside a 2s backoff policy."""
        clock = ManualClock()
        router, fake, sink = build_router(
            clock,
            [httpx.Response(429, headers={"Retry-After": "30"}), ok()],
        )

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        # The peer returned a transient 429; declining the retry must not relabel it
        # as our own budget exhaustion.
        assert excinfo.value.failure_class is FailureClass.HTTP_TRANSIENT
        assert excinfo.value.status_code == 429
        assert fake.call_count == 1
        assert clock.sleeps == []
        assert sink.outcomes == [REQUEST_OUTCOME_FAILED]
        assert sink.requests[0]["retries"] == 0


# ── 3. transient failure, budget exhausted ─────────────────────────────────


class TestBudgetExhausted:
    def test_deadline_stops_a_retry_that_cannot_fit(self):
        """One slow first attempt consumes the budget, so no retry is issued."""
        clock = ManualClock()
        policy = RetryPolicy(max_attempts=3, total_deadline_seconds=5.0, per_attempt_timeout_seconds=4.0)
        router, fake, sink = build_router(
            clock,
            [
                lambda _request: (clock.advance(5.0), httpx.Response(503))[1],
                ok(),
            ],
            policy=policy,
        )

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.failure_class is FailureClass.BUDGET_EXHAUSTED
        assert excinfo.value.attempts == 1
        assert fake.call_count == 1, "no retry may be issued once the deadline is spent"
        assert sink.outcomes == [REQUEST_OUTCOME_BUDGET_EXHAUSTED]

    def test_budget_exhaustion_is_distinct_from_a_retryable_failure(self):
        clock = ManualClock()
        policy = RetryPolicy(max_attempts=3, total_deadline_seconds=5.0, per_attempt_timeout_seconds=4.0)
        router, _fake, _sink = build_router(
            clock,
            [
                lambda _request: (clock.advance(5.0), httpx.Response(503))[1],
                ok(),
            ],
            policy=policy,
        )

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.retryable is False

    def test_backoff_larger_than_the_remaining_budget_stops_the_retry(self):
        clock = ManualClock()
        policy = RetryPolicy(
            max_attempts=3,
            base_delay_seconds=2.0,
            max_delay_seconds=2.0,
            total_deadline_seconds=3.0,
            per_attempt_timeout_seconds=1.0,
        )
        router, fake, sink = build_router(
            clock,
            [
                lambda _request: (clock.advance(1.5), httpx.Response(503))[1],
                ok(),
            ],
            policy=policy,
        )

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.failure_class is FailureClass.BUDGET_EXHAUSTED
        # 1.5s spent + 2.0s backoff would exceed the 3.0s deadline.
        assert fake.call_count == 1
        assert clock.sleeps == []
        assert sink.outcomes == [REQUEST_OUTCOME_BUDGET_EXHAUSTED]

    def test_per_attempt_timeout_is_clamped_to_the_remaining_budget(self):
        """A generous per-attempt timeout must not extend a request past the deadline."""
        clock = ManualClock()
        policy = RetryPolicy(
            max_attempts=2,
            total_deadline_seconds=6.0,
            per_attempt_timeout_seconds=30.0,
            base_delay_seconds=0.0,
            max_delay_seconds=0.0,
        )
        seen: list[float] = []

        def _record(request):
            seen.append(request.extensions["timeout"]["read"])
            clock.advance(3.0)
            return httpx.Response(503)

        router, _fake, _sink = build_router(clock, [_record, _record], policy=policy)

        with pytest.raises(VLLMGenerationError):
            router.route_chat("gen_4b", messages=MESSAGES)

        assert seen == [6.0, 3.0]

    def test_total_elapsed_never_exceeds_the_deadline(self):
        clock = ManualClock()
        policy = RetryPolicy(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=0.5, total_deadline_seconds=4.0)
        router, fake, _sink = build_router(
            clock,
            [httpx.Response(503), httpx.Response(503), httpx.Response(503), ok()],
            policy=policy,
        )

        with pytest.raises(VLLMGenerationError):
            router.route_chat("gen_4b", messages=MESSAGES)

        assert clock.now <= policy.total_deadline_seconds
        assert fake.call_count == 3


# ── 4. permanent 4xx is never retried ──────────────────────────────────────


class TestPermanentClientErrors:
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 409, 413, 415, 422])
    def test_ordinary_4xx_is_never_retried(self, status):
        clock = ManualClock()
        router, fake, sink = build_router(clock, [httpx.Response(status), ok()])

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.failure_class is FailureClass.HTTP_PERMANENT
        assert excinfo.value.status_code == status
        assert excinfo.value.attempts == 1
        assert excinfo.value.retryable is False
        assert fake.call_count == 1, "an ordinary 4xx must never be retried"
        assert clock.sleeps == []
        assert sink.failures == [FailureClass.HTTP_PERMANENT.value]
        assert sink.outcomes == [REQUEST_OUTCOME_FAILED]

    def test_404_from_the_vllm_router_is_permanent(self):
        clock = ManualClock()
        router, fake, _sink = build_router(clock, [httpx.Response(404), ok()])

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.failure_class is FailureClass.HTTP_PERMANENT
        assert fake.call_count == 1

    def test_a_500_is_permanent_by_decision(self):
        """500 is deliberately off the transient list; see TRANSIENT_HTTP_STATUSES."""
        clock = ManualClock()
        router, fake, _sink = build_router(clock, [httpx.Response(500), ok()])

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.failure_class is FailureClass.HTTP_PERMANENT
        assert fake.call_count == 1

    def test_malformed_response_is_not_retried(self):
        clock = ManualClock()
        router, fake, sink = build_router(clock, [ok()])
        fake.script = [httpx.Response(200, json={"choices": []}), ok()]

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.failure_class is FailureClass.MALFORMED_RESPONSE
        assert excinfo.value.retryable is False
        assert fake.call_count == 1
        assert sink.failures == [FailureClass.MALFORMED_RESPONSE.value]

    def test_unknown_endpoint_is_a_configuration_failure_without_any_request(self):
        clock = ManualClock()
        router, fake, sink = build_router(clock, [])

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_99b", messages=MESSAGES)

        assert excinfo.value.failure_class is FailureClass.CONFIGURATION
        assert excinfo.value.attempts == 0
        assert fake.call_count == 0
        assert sink.attempts == []
        assert sink.outcomes == [REQUEST_OUTCOME_FAILED]

    def test_an_unrecognised_exception_is_not_retried(self):
        clock = ManualClock()
        router, fake, sink = build_router(clock, [RuntimeError("something new"), ok()])

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.failure_class is FailureClass.UNKNOWN
        assert excinfo.value.retryable is False
        assert fake.call_count == 1
        assert sink.failures == [FailureClass.UNKNOWN.value]


# ── 5. the attempt cap is a hard structural bound ──────────────────────────


class TestAttemptCap:
    def test_attempts_stop_at_max_attempts(self):
        clock = ManualClock()
        policy = RetryPolicy(max_attempts=3, base_delay_seconds=0.0, max_delay_seconds=0.0)
        router, fake, sink = build_router(
            clock,
            [httpx.Response(503), httpx.Response(503), httpx.Response(503), ok()],
            policy=policy,
        )

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.failure_class is FailureClass.HTTP_TRANSIENT
        assert excinfo.value.attempts == 3
        assert fake.call_count == 3
        assert len(sink.attempts) == 3
        assert sink.requests[0]["retries"] == 2

    def test_single_attempt_policy_never_retries(self):
        clock = ManualClock()
        policy = RetryPolicy(max_attempts=1)
        router, fake, _sink = build_router(clock, [httpx.Response(503), ok()], policy=policy)

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.attempts == 1
        assert fake.call_count == 1

    def test_env_cannot_raise_the_attempt_cap_above_the_hard_bound(self, monkeypatch):
        monkeypatch.setenv("VLLM_MAX_ATTEMPTS", "500")

        policy = RetryPolicy.from_env(per_attempt_timeout_seconds=10.0)

        assert policy.max_attempts == HARD_MAX_ATTEMPTS

    def test_env_cannot_lower_the_attempt_cap_below_one(self, monkeypatch):
        monkeypatch.setenv("VLLM_MAX_ATTEMPTS", "0")

        policy = RetryPolicy.from_env(per_attempt_timeout_seconds=10.0)

        assert policy.max_attempts == 1

    def test_policy_rejects_an_attempt_count_outside_the_bound(self):
        with pytest.raises(ValueError):
            RetryPolicy(max_attempts=HARD_MAX_ATTEMPTS + 1)
        with pytest.raises(ValueError):
            RetryPolicy(max_attempts=0)

    def test_total_retry_time_is_capped_by_the_backoff_schedule(self):
        clock = ManualClock()
        policy = RetryPolicy(max_attempts=3, base_delay_seconds=10.0, max_delay_seconds=10.0)
        router, fake, _sink = build_router(clock, [httpx.Response(503)] * 4, policy=policy)

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        # The first 10s backoff fits inside the 15s default deadline, the second
        # does not — so the request stops with its own budget, not with a shorter
        # or compressed backoff.
        assert excinfo.value.failure_class is FailureClass.BUDGET_EXHAUSTED
        assert clock.sleeps == [10.0]
        assert fake.call_count == 2
        assert clock.now <= 15.0


# ── 6/7. timeout and connection handling ───────────────────────────────────


class TestTimeoutAndConnectionHandling:
    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectTimeout("connect timed out"),
            httpx.ReadTimeout("read timed out"),
            httpx.WriteTimeout("write timed out"),
            httpx.PoolTimeout("pool timed out"),
        ],
    )
    def test_every_timeout_subclass_is_classified_as_timeout(self, exc):
        clock = ManualClock()
        router, _fake, sink = build_router(clock, [exc, ok()])

        router.route_chat("gen_4b", messages=MESSAGES)

        assert sink.failures == [FailureClass.TIMEOUT.value]

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("connection refused"),
            httpx.ReadError("peer reset"),
            httpx.WriteError("broken pipe"),
        ],
    )
    def test_every_network_subclass_is_classified_as_connection(self, exc):
        clock = ManualClock()
        router, _fake, sink = build_router(clock, [exc, ok()])

        router.route_chat("gen_4b", messages=MESSAGES)

        assert sink.failures == [FailureClass.CONNECTION.value]

    def test_a_persistent_timeout_gives_up_with_the_last_classification(self):
        clock = ManualClock()
        policy = RetryPolicy(max_attempts=2, base_delay_seconds=0.0, max_delay_seconds=0.0)
        router, fake, _sink = build_router(
            clock,
            [httpx.ReadTimeout("first"), httpx.ReadTimeout("second"), ok()],
            policy=policy,
        )

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.failure_class is FailureClass.TIMEOUT
        assert excinfo.value.attempts == 2
        assert fake.call_count == 2

    def test_a_persistent_connection_failure_gives_up(self):
        clock = ManualClock()
        policy = RetryPolicy(max_attempts=2, base_delay_seconds=0.0, max_delay_seconds=0.0)
        router, fake, _sink = build_router(
            clock,
            [httpx.ConnectError("first"), httpx.ConnectError("second"), ok()],
            policy=policy,
        )

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.failure_class is FailureClass.CONNECTION
        assert fake.call_count == 2


# ── 8. malformed vLLM responses ────────────────────────────────────────────


class TestMalformedResponses:
    @pytest.mark.parametrize(
        ("payload", "label"),
        [
            (httpx.Response(200, content=b"not json at all"), "invalid json"),
            (httpx.Response(200, json={"choices": []}), "no choices"),
            (httpx.Response(200, json={}), "no choices key"),
            (httpx.Response(200, json={"choices": [{}]}), "no message"),
            (httpx.Response(200, json={"choices": [{"message": {}}]}), "no content"),
            (
                httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
                "null content",
            ),
            (
                httpx.Response(200, json={"choices": [{"message": {"content": {"a": 1}}}]}),
                "non-string content",
            ),
        ],
    )
    def test_malformed_bodies_are_classified_and_never_retried(self, payload, label):
        clock = ManualClock()
        router, fake, _sink = build_router(clock, [payload, ok()])

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_4b", messages=MESSAGES)

        assert excinfo.value.failure_class is FailureClass.MALFORMED_RESPONSE, label
        assert fake.call_count == 1, label
        assert excinfo.value.attempts == 1


# ── 9. metrics are counted exactly once, on the existing collector ─────────


class TestMetricsExactlyOnce:
    def _collector(self):
        from monitoring.otel_tracer import MetricsCollector

        return MetricsCollector()

    def _counters(self, collector):
        return dict(collector._counters)

    def test_success_counts_one_attempt_and_one_request(self):
        collector = self._collector()
        sink = _CollectorSink(collector)

        clock = ManualClock()
        router, _fake, _sink = build_router(clock, [ok()])
        router._metrics = sink

        router.route_chat("gen_4b", messages=MESSAGES)
        counters = self._counters(collector)

        assert counters["vllm.generation.attempts"] == 1
        assert counters["vllm.generation.attempt.success"] == 1
        assert counters.get("vllm.generation.attempt.failed", 0) == 0
        assert counters["vllm.generation.requests"] == 1
        assert counters["vllm.generation.outcome.success"] == 1
        assert "vllm.generation.retries" not in counters

    def test_retry_counts_two_attempts_and_one_request(self):
        collector = self._collector()
        sink = _CollectorSink(collector)

        clock = ManualClock()
        router, _fake, _sink = build_router(clock, [httpx.Response(503), ok()])
        router._metrics = sink

        router.route_chat("gen_4b", messages=MESSAGES)
        counters = self._counters(collector)

        assert counters["vllm.generation.attempts"] == 2
        assert counters["vllm.generation.attempt.success"] == 1
        assert counters["vllm.generation.attempt.failed"] == 1
        assert counters["vllm.generation.attempt.failure_class.http_transient"] == 1
        assert counters["vllm.generation.requests"] == 1
        assert counters["vllm.generation.outcome.success"] == 1
        assert counters["vllm.generation.retries"] == 1

    def test_permanent_failure_counts_one_attempt_and_one_request(self):
        collector = self._collector()
        sink = _CollectorSink(collector)

        clock = ManualClock()
        router, _fake, _sink = build_router(clock, [httpx.Response(403), ok()])
        router._metrics = sink

        with pytest.raises(VLLMGenerationError):
            router.route_chat("gen_4b", messages=MESSAGES)
        counters = self._counters(collector)

        assert counters["vllm.generation.attempts"] == 1
        assert counters["vllm.generation.attempt.failed"] == 1
        assert counters["vllm.generation.attempt.failure_class.http_permanent"] == 1
        assert counters["vllm.generation.requests"] == 1
        assert counters["vllm.generation.outcome.failed"] == 1
        assert "vllm.generation.retries" not in counters

    def test_budget_exhaustion_counts_one_request_with_its_own_outcome(self):
        collector = self._collector()
        sink = _CollectorSink(collector)

        clock = ManualClock()
        policy = RetryPolicy(max_attempts=3, total_deadline_seconds=5.0, per_attempt_timeout_seconds=4.0)
        router, _fake, _sink = build_router(
            clock,
            [lambda _r: (clock.advance(5.0), httpx.Response(503))[1], ok()],
            policy=policy,
        )
        router._metrics = sink

        with pytest.raises(VLLMGenerationError):
            router.route_chat("gen_4b", messages=MESSAGES)
        counters = self._counters(collector)

        assert counters["vllm.generation.requests"] == 1
        assert counters["vllm.generation.outcome.budget_exhausted"] == 1
        assert counters["vllm.generation.attempts"] == 1

    def test_request_outcomes_always_sum_to_the_request_count(self):
        collector = self._collector()
        sink = _CollectorSink(collector)

        scripts = [
            [ok()],
            [httpx.Response(503), ok()],
            [httpx.Response(404)],
            [httpx.ConnectError("refused"), ok()],
        ]
        for script in scripts:
            clock = ManualClock()
            router, _fake, _sink = build_router(clock, script)
            router._metrics = sink
            try:
                router.route_chat("gen_4b", messages=MESSAGES)
            except VLLMGenerationError:
                pass

        counters = self._counters(collector)
        outcomes = sum(value for name, value in counters.items() if name.startswith("vllm.generation.outcome."))

        assert outcomes == counters["vllm.generation.requests"] == len(scripts)
        assert counters["vllm.generation.attempts"] == (
            counters["vllm.generation.attempt.success"] + counters["vllm.generation.attempt.failed"]
        )

    def test_metrics_reach_the_prometheus_exposition(self):
        """The counters land on /api/metrics's own exporter, not a second registry."""
        collector = self._collector()
        sink = _CollectorSink(collector)

        clock = ManualClock()
        router, _fake, _sink = build_router(clock, [httpx.Response(503), ok()])
        router._metrics = sink

        router.route_chat("gen_14b", messages=MESSAGES)
        text = collector.to_prometheus_text()

        assert "rag_vllm_generation_attempts 2" in text
        assert "rag_vllm_generation_attempt_success 1" in text
        assert "rag_vllm_generation_attempt_failed 1" in text
        assert "rag_vllm_generation_attempt_failure_class_http_transient 1" in text
        assert "rag_vllm_generation_requests 1" in text
        assert "rag_vllm_generation_outcome_success 1" in text
        assert "rag_vllm_generation_retries 1" in text

    def test_a_successful_attempt_emits_no_failure_class_series(self):
        collector = self._collector()
        sink = _CollectorSink(collector)

        clock = ManualClock()
        router, _fake, _sink = build_router(clock, [ok()])
        router._metrics = sink

        router.route_chat("gen_4b", messages=MESSAGES)

        assert not [name for name in collector._counters if "failure_class" in name]

    def test_a_metrics_failure_never_breaks_the_request(self):
        """Metrics are not allowed to become a reason a generation request fails."""

        class _ExplodingSink(RecordingSink):
            def record_attempt(self, **_kwargs):
                raise RuntimeError("metrics backend down")

            def record_request(self, **_kwargs):
                raise RuntimeError("metrics backend down")

        clock = ManualClock()
        router, _fake, _sink = build_router(clock, [ok()])
        router._metrics = _ExplodingSink()

        assert router.route_chat("gen_4b", messages=MESSAGES)["content"]

    def test_the_canonical_sink_survives_an_absent_api_layer(self, monkeypatch):
        """The default sink is defensive: a metrics problem is swallowed."""
        import builtins

        real_import = builtins.__import__

        def _blocked(name, *args, **kwargs):
            if name == "api.routes":
                raise ImportError("api layer unavailable")
            return real_import(name, *args, **kwargs)

        clock = ManualClock()
        router, _fake, _sink = build_router(clock, [ok()])
        router._metrics = _default_sink()
        monkeypatch.setattr(builtins, "__import__", _blocked)

        assert router.route_chat("gen_4b", messages=MESSAGES)["content"]


class _CollectorSink:
    """Publishes straight onto the canonical collector, as production does."""

    def __init__(self, collector) -> None:
        self.collector = collector

    def record_attempt(self, *, succeeded: bool, failure_class: str | None) -> None:
        self.collector.record_vllm_generation_attempt(succeeded=succeeded, failure_class=failure_class)

    def record_request(self, *, outcome: str, retries: int, elapsed_ms: float) -> None:
        self.collector.record_vllm_generation_request(outcome=outcome, retries=retries, elapsed_ms=elapsed_ms)


def _default_sink():
    from router.stateless_router import _CanonicalVLLMMetricsSink

    return _CanonicalVLLMMetricsSink()


# ── 10. no secret leakage ──────────────────────────────────────────────────


class TestNoSecretLeakage:
    CREDENTIAL_URL = "http://gpu-operator:s3cr3t-token@internal-vllm:8100"

    def _assert_clean(self, error: VLLMGenerationError, *forbidden: str) -> None:
        surfaces = [
            str(error),
            repr(error),
            repr(error.args),
            repr(error.to_dict()),
            repr(error.extra),
            error.failure_class.value,
            error.endpoint_key,
        ]
        for surface in surfaces:
            for needle in (*forbidden, self.CREDENTIAL_URL, "http://", "internal-vllm", "s3cr3t", "gpu-operator"):
                assert needle not in surface, (needle, surface)

    def _router_with_credentialed_endpoint(self, clock, script):
        router, fake, sink = build_router(clock, script)
        router.endpoints["gen_14b"] = self.CREDENTIAL_URL
        return router, fake, sink

    def test_connection_error_message_never_reaches_the_caller(self):
        clock = ManualClock()
        router, _fake, _sink = self._router_with_credentialed_endpoint(
            clock,
            [httpx.ConnectError(f"[Errno 111] Connection refused to {self.CREDENTIAL_URL}/v1/chat/completions")],
        )

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_14b", messages=MESSAGES)

        self._assert_clean(excinfo.value)

    def test_timeout_message_never_reaches_the_caller(self):
        clock = ManualClock()
        router, _fake, _sink = self._router_with_credentialed_endpoint(
            clock,
            [httpx.ReadTimeout(f"timeout while reading {self.CREDENTIAL_URL}")],
        )

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_14b", messages=MESSAGES)

        self._assert_clean(excinfo.value)

    def test_http_error_message_never_reaches_the_caller(self):
        clock = ManualClock()
        router, _fake, _sink = self._router_with_credentialed_endpoint(
            clock,
            [httpx.Response(503, text=f"upstream {self.CREDENTIAL_URL} overloaded")],
        )

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_14b", messages=MESSAGES)

        self._assert_clean(excinfo.value)

    def test_a_caller_supplied_endpoint_key_cannot_smuggle_a_url(self):
        clock = ManualClock()
        router, _fake, _sink = build_router(clock, [])

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat(f"http://user:hunter2@evil.internal/{self.CREDENTIAL_URL}", messages=MESSAGES)

        assert excinfo.value.endpoint_key == "redacted"
        assert "hunter2" not in str(excinfo.value)
        assert "evil.internal" not in str(excinfo.value)

    def test_the_raw_transport_exception_is_not_in_the_exception_chain(self):
        """A formatter walking __cause__ must not be able to reach the URL."""
        clock = ManualClock()
        router, _fake, _sink = self._router_with_credentialed_endpoint(
            clock,
            [httpx.ConnectError(f"refused {self.CREDENTIAL_URL}")] * 2,
        )

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_14b", messages=MESSAGES)

        assert excinfo.value.__cause__ is None
        assert excinfo.value.__suppress_context__ is True

    def test_malformed_response_body_never_reaches_the_caller(self):
        clock = ManualClock()
        router, _fake, _sink = self._router_with_credentialed_endpoint(
            clock,
            [
                httpx.Response(
                    200,
                    json={"error": f"internal token {self.CREDENTIAL_URL}"},
                )
            ],
        )

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_14b", messages=MESSAGES)

        self._assert_clean(excinfo.value, "internal token")

    def test_the_sanitised_message_still_carries_the_diagnosis(self):
        """Redaction must not cost the operator the actionable fields."""
        clock = ManualClock()
        router, _fake, _sink = build_router(clock, [httpx.Response(429), httpx.Response(429)])

        with pytest.raises(VLLMGenerationError) as excinfo:
            router.route_chat("gen_14b", messages=MESSAGES)

        message = str(excinfo.value)
        assert "class=http_transient" in message
        assert "endpoint=gen_14b" in message
        assert "status=429" in message
        assert "attempts=2" in message


# ── 11. no silent model substitution on retry ──────────────────────────────


class TestNoSilentModelSubstitution:
    def test_a_retry_never_switches_endpoint(self):
        """A failing 14B request must not be answered by 4B behind the operator's back."""
        clock = ManualClock()
        router, fake, _sink = build_router(clock, [httpx.Response(503), ok()])

        router.route_chat("gen_14b", messages=MESSAGES)

        assert fake.base_urls == ["http://vllm-14b.internal:8100", "http://vllm-14b.internal:8100"]

    def test_a_permanently_failing_14b_request_raises_instead_of_downgrading(self):
        clock = ManualClock()
        policy = RetryPolicy(max_attempts=2, base_delay_seconds=0.0, max_delay_seconds=0.0)
        router, fake, _sink = build_router(
            clock,
            [httpx.ConnectError("refused"), httpx.ConnectError("refused"), ok()],
            policy=policy,
        )

        with pytest.raises(VLLMGenerationError):
            router.route_chat("gen_14b", messages=MESSAGES)

        assert set(fake.base_urls) == {"http://vllm-14b.internal:8100"}
        assert fake.call_count == 2

    def test_production_routing_still_resolves_14b_to_the_14b_endpoint(self):
        """The retry contract must not have relaxed the existing routing rule."""
        from unittest.mock import MagicMock, patch

        from models.llm_client import LLMClient

        client = LLMClient.__new__(LLMClient)
        client._router = MagicMock()
        client.max_conversation_rounds = 6
        client.prompt_version = "v2.1"

        with patch("common.config.is_production_mode", return_value=True):
            assert client._resolve_endpoint("qwen3-14b") == "gen_14b"
        with patch("common.config.is_production_mode", return_value=False):
            assert client._resolve_endpoint("qwen3-14b") == "gen_4b"

    def test_the_client_and_router_agree_on_the_failure_type(self):
        """A real (mock-transport) LLMClient.generate propagates the typed failure."""
        from unittest.mock import patch

        from models.llm_client import LLMClient

        clock = ManualClock()
        policy = RetryPolicy(max_attempts=1)
        router, _fake, _sink = build_router(clock, [httpx.Response(400)], policy=policy)

        client = LLMClient.__new__(LLMClient)
        client._router = router
        client.max_conversation_rounds = 6
        client.prompt_version = "v2.1"

        ctx = _minimal_ctx()
        with (
            patch("common.config.is_production_mode", return_value=True),
            pytest.raises(VLLMGenerationError) as excinfo,
        ):
            client.generate(ctx, target_model="qwen3-14b", max_tokens=128)

        assert excinfo.value.failure_class is FailureClass.HTTP_PERMANENT
        assert excinfo.value.endpoint_key == "gen_14b"

    def test_the_existing_client_success_path_is_unchanged(self):
        from models.llm_client import LLMClient

        clock = ManualClock()
        router, _fake, _sink = build_router(clock, [ok("烟酰胺推荐浓度为 2-5%。")])

        client = LLMClient.__new__(LLMClient)
        client._router = router
        client.max_conversation_rounds = 6
        client.prompt_version = "v2.1"

        ctx = _minimal_ctx()
        result = client.generate(ctx, target_model="qwen3-14b", max_tokens=128)

        assert result.answer == "烟酰胺推荐浓度为 2-5%。"
        assert result.model_used == "qwen3-14b"
        assert result.has_more is False
        assert ctx.prefix_cache_hit is None


def _minimal_ctx():
    from core.pipeline_context import (
        EvidenceGateResult,
        QueryRewriteResult,
        RequestContext,
        RerankResult,
    )

    ctx = RequestContext(user_input="烟酰胺推荐浓度是多少？", session_id=None, user_id="u")
    ctx.rewrite_result = QueryRewriteResult(
        rewritten_query="烟酰胺推荐浓度",
        business_type="development",
        intent="ingredient",
        requires_context=True,
    )
    ctx.rerank_results = [RerankResult(doc_id="d1", content="烟酰胺推荐浓度 2-5%", final_score=0.95)]
    ctx.evidence_result = EvidenceGateResult(
        evidence_score=0.85,
        ce_top1_score=0.9,
        ce_top3_mean_score=0.85,
        retrieval_agreement_score=0.8,
        doc_consistency_score=0.9,
        decision="pass",
        top_docs=ctx.rerank_results,
    )
    ctx.user_role_mask = 0
    ctx.user_dept_mask = 0
    ctx.max_output_tokens = 128
    return ctx


# ── 12. the taxonomy itself ────────────────────────────────────────────────


class TestFailureTaxonomy:
    def test_only_three_classes_are_retryable(self):
        retryable = {
            failure_class.value
            for failure_class in FailureClass
            if failure_class
            in {
                FailureClass.TIMEOUT,
                FailureClass.CONNECTION,
                FailureClass.HTTP_TRANSIENT,
            }
        }

        assert retryable == {"timeout", "connection", "http_transient"}

    @pytest.mark.parametrize("status", sorted(TRANSIENT_HTTP_STATUSES))
    def test_allow_listed_statuses_classify_as_transient(self, status):
        assert classify_http_status(status) is FailureClass.HTTP_TRANSIENT

    @pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 405, 409, 410, 413, 415, 422, 501, 505])
    def test_every_other_status_classifies_as_permanent(self, status):
        assert classify_http_status(status) is FailureClass.HTTP_PERMANENT

    def test_500_is_not_on_the_transient_allow_list(self):
        assert 500 not in TRANSIENT_HTTP_STATUSES

    def test_transport_classification_defaults_to_unknown(self):
        assert classify_transport_exception(RuntimeError("x")) is FailureClass.UNKNOWN
        assert classify_transport_exception(ValueError("x")) is FailureClass.UNKNOWN

    def test_response_classification_maps_payload_errors(self):
        assert classify_response_exception(KeyError("choices")) is FailureClass.MALFORMED_RESPONSE
        assert classify_response_exception(IndexError("list")) is FailureClass.MALFORMED_RESPONSE
        assert classify_response_exception(ValueError("json")) is FailureClass.MALFORMED_RESPONSE

    def test_response_classification_reads_the_status_of_an_http_error(self):
        request = httpx.Request("POST", "http://vllm-4b.internal:8101/v1/chat/completions")
        transient = httpx.HTTPStatusError("boom", request=request, response=httpx.Response(503, request=request))
        permanent = httpx.HTTPStatusError("boom", request=request, response=httpx.Response(404, request=request))

        assert classify_response_exception(transient) is FailureClass.HTTP_TRANSIENT
        assert classify_response_exception(permanent) is FailureClass.HTTP_PERMANENT

    def test_retry_after_only_accepts_a_non_negative_finite_number(self):
        assert parse_retry_after_seconds({"retry-after": "5"}) == 5.0
        assert parse_retry_after_seconds({"retry-after": "-1"}) is None
        assert parse_retry_after_seconds({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
        assert parse_retry_after_seconds({"retry-after": "inf"}) is None
        assert parse_retry_after_seconds({}) is None

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("gen_4b", "gen_4b"),
            ("gen_14b", "gen_14b"),
            ("a.b-c_1", "a.b-c_1"),
            ("http://x/y", "redacted"),
            ("user:pass@host", "redacted"),
            ("", "redacted"),
            ("x" * 65, "redacted"),
            ("gen 4b", "redacted"),
        ],
    )
    def test_endpoint_key_sanitisation(self, value, expected):
        assert sanitize_endpoint_key(value) == expected

    def test_policy_rejects_inverted_backoff_bounds(self):
        with pytest.raises(ValueError):
            RetryPolicy(base_delay_seconds=5.0, max_delay_seconds=1.0)

    def test_policy_rejects_a_non_positive_deadline(self):
        with pytest.raises(ValueError):
            RetryPolicy(total_deadline_seconds=0.0)

    def test_env_overrides_are_clamped(self, monkeypatch):
        monkeypatch.setenv("VLLM_RETRY_BASE_DELAY_SECONDS", "9999")
        monkeypatch.setenv("VLLM_RETRY_MAX_DELAY_SECONDS", "9999")
        monkeypatch.setenv("VLLM_GENERATION_DEADLINE_SECONDS", "0.0001")

        policy = RetryPolicy.from_env(per_attempt_timeout_seconds=10.0)

        assert policy.base_delay_seconds == 5.0
        assert policy.max_delay_seconds == 30.0
        assert policy.total_deadline_seconds == 0.1

    def test_a_malformed_env_value_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("VLLM_MAX_ATTEMPTS", "many")

        policy = RetryPolicy.from_env(per_attempt_timeout_seconds=10.0)

        assert policy.max_attempts == 2

    def test_defaults_are_bounded(self):
        policy = RetryPolicy()

        assert 1 <= policy.max_attempts <= HARD_MAX_ATTEMPTS
        assert policy.max_delay_seconds <= policy.total_deadline_seconds


# ── 13. the module documents its own scope boundary ────────────────────────


class TestScopeBoundary:
    def test_the_rewrite_completion_path_is_still_single_attempt(self):
        """route_completion is the rewrite path and is explicitly out of scope."""
        clock = ManualClock()
        router, fake, _sink = build_router(clock, [httpx.Response(503), ok()])

        with pytest.raises(httpx.HTTPStatusError):
            router.route_completion("gen_4b", "rewrite prompt")

        assert fake.call_count == 1

    def test_health_probes_are_unchanged(self):
        clock = ManualClock()
        router, fake, _sink = build_router(clock, [httpx.Response(200), httpx.Response(200)])

        assert router.get_health("gen_4b") is True
        assert router.check_any_endpoint_alive() is True
        assert fake.call_count == 2
        assert fake.requests[0].url.path == "/health"
