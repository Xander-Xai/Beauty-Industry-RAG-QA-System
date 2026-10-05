"""
Bounded resilience contract for the canonical vLLM generation path.

This module owns the *decision* half of the contract: which failures may be
retried, how many times, and for how long in total. `router/stateless_router.py`
owns the *transport* half. Keeping them apart is what makes the policy testable
without a vLLM server, a GPU, or a socket.

What the contract guarantees, and why each bound exists:

* **A failure taxonomy, not a catch-all.** Every non-2xx response and every
  transport exception maps to exactly one :class:`FailureClass`. Retrying is a
  property of the class, so "should this be retried" is answered in one place
  instead of at each call site.
* **A hard attempt cap.** ``max_attempts`` is clamped to
  :data:`HARD_MAX_ATTEMPTS` (3 total attempts, i.e. at most 2 extra retries);
  the shipped default is :data:`DEFAULT_MAX_ATTEMPTS` (2 total attempts). An
  operator cannot turn this into a retry storm by setting an environment
  variable, because the clamp is structural rather than a convention.
* **A total request deadline.** The deadline covers *all* attempts plus every
  backoff sleep, and each attempt's timeout is additionally clamped to the
  budget still remaining. A deadline that only counts attempts would let a
  single slow retry extend the request indefinitely.
* **Only an explicit allow-list is retried.** 408/429/502/503/504 are transient.
  Every other non-2xx status — every ordinary 4xx and 500 included — is permanent,
  because this repository holds no endpoint-specific evidence that replaying them
  is safe. Not being named is a fail-closed default, not a claim about what the
  peer would have done.
* **Fail closed on the unrecognised.** An exception this module does not
  recognise is classified :data:`FailureClass.UNKNOWN` and is *not* retried.
  Retry is something a failure has to earn.
* **No silent model substitution.** Nothing here can change which endpoint a
  request targets. The endpoint is resolved once by the caller and every attempt
  reuses it, so a failing 14B request cannot quietly be answered by 4B.

The failure type is sanitised by construction: :meth:`VLLMGenerationError.__str__`
renders only a fixed template filled with the failure class, the endpoint *config
key*, the HTTP status and the attempt count. Endpoint URLs, credentials, tokens
and response bodies are never interpolated into it, and the originating transport
exception is deliberately kept out of the exception chain so that no formatter
walking ``__cause__`` can turn a URL with an embedded credential into an API
response.

Evidence level: REPO_VERIFIED (contract + deterministic tests). No real vLLM
server, GPU or benchmark is exercised here; that remains PENDING external
validation and is not claimed.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

import httpx

from common.exceptions import GenerationError

logger = logging.getLogger(__name__)


class FailureClass(str, Enum):
    """The closed set of ways a vLLM generation attempt can fail.

    The value is the stable token used in metrics, so renaming a member is a
    metric-name change and not a silent reclassification.
    """

    #: The attempt exceeded its (budget-clamped) timeout. Transient.
    TIMEOUT = "timeout"
    #: The endpoint refused, reset, or dropped the connection. Transient.
    CONNECTION = "connection"
    #: An HTTP status on the explicit transient allow-list. Transient.
    HTTP_TRANSIENT = "http_transient"
    #: Any other non-2xx status, including every ordinary 4xx. Permanent.
    HTTP_PERMANENT = "http_permanent"
    #: A 2xx response whose body is not a usable completion. Permanent.
    MALFORMED_RESPONSE = "malformed_response"
    #: The endpoint key is not configured in this deployment. Permanent.
    CONFIGURATION = "configuration"
    #: The total deadline or the attempt cap stopped the request. Terminal, and a
    #: property of this process's budget rather than of the peer.
    BUDGET_EXHAUSTED = "budget_exhausted"
    #: Unrecognised failure. Permanent; nothing unknown is ever retried.
    UNKNOWN = "unknown"


#: The only classes a retry is allowed for. Membership here *is* the retry
#: policy; ``FailureClass`` has no other retry attribute, so adding a new member
#: cannot accidentally make it retryable.
RETRYABLE_FAILURE_CLASSES = frozenset(
    {
        FailureClass.TIMEOUT,
        FailureClass.CONNECTION,
        FailureClass.HTTP_TRANSIENT,
    }
)

#: HTTP statuses whose semantics are explicitly "the request may be repeated".
#:
#: This is an allow-list, not a denial-list: a status is retried only because it is
#: named here. 408 Request Timeout and 429 Too Many Requests are transient by
#: definition. 502/503/504 are the gateway/unavailable/timeout statuses a vLLM
#: server emits while starting, reloading, or shedding load. This mirrors the
#: transient set already used by ``common/http_client.py`` so the two
#: service-to-service paths cannot drift apart silently.
#:
#: 500 is deliberately **absent**, and the reason is an absence of evidence rather
#: than a claim about the peer. HTTP 500 semantics are heterogeneous — the same
#: status can carry a single rejected request or a server-wide fault — and this
#: repository contains no endpoint-specific observation showing that replaying a
#: 500 is safe against a vLLM endpoint. So 500 stays fail-closed, under the same
#: rule that governs :data:`FailureClass.UNKNOWN`: unknown or unproven semantics
#: are not retried. Widening this allow-list requires real runtime evidence, not
#: a stronger guess. Absence is a decision, not an oversight.
TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 502, 503, 504})

#: Upper bound on total attempts (first try included) that no environment
#: variable may raise. This is the structural anti-storm bound. The invariant is
#: two-part: the default is :data:`DEFAULT_MAX_ATTEMPTS` (2) total attempts, i.e.
#: at most 1 extra retry, and no configuration can exceed :data:`HARD_MAX_ATTEMPTS`
#: (3) total attempts, i.e. at most 2 extra retries.
HARD_MAX_ATTEMPTS = 3

DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RETRY_BASE_DELAY_SECONDS = 0.2
DEFAULT_RETRY_MAX_DELAY_SECONDS = 2.0
DEFAULT_GENERATION_DEADLINE_SECONDS = 15.0
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 10.0

#: An endpoint key is a config identifier such as ``gen_4b``. It is echoed into
#: the sanitised error message, so anything that is not plainly an identifier is
#: dropped rather than passed through: ``endpoint_key`` reaches this layer from
#: caller code, and a caller must not be able to smuggle a URL — or a credential
#: inside one — into an error body.
_SAFE_ENDPOINT_KEY_RE = re.compile(r"\A[A-Za-z0-9_.-]{1,64}\Z")

REDACTED = "redacted"


def sanitize_endpoint_key(value: object) -> str:
    """Return ``value`` when it is a safe config key, otherwise a fixed marker."""
    text = str(value)
    return text if _SAFE_ENDPOINT_KEY_RE.match(text) else REDACTED


def _env_float(name: str, default: float, low: float, high: float) -> float:
    """Read a float from the environment, clamped to ``[low, high]``.

    A malformed or non-finite value falls back to the default rather than
    propagating: configuration must not be able to break the request path.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r is not a number; using %s", name, raw, default)
        return default
    if not math.isfinite(value):
        logger.warning("%s=%r is not finite; using %s", name, raw, default)
        return default
    return min(max(value, low), high)


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r is not an integer; using %s", name, raw, default)
        return default
    return min(max(value, low), high)


@dataclass(frozen=True)
class RetryPolicy:
    """The bounded retry contract for one endpoint.

    ``per_attempt_timeout_seconds`` is the pre-existing per-request timeout.
    ``total_deadline_seconds`` is the new, coarser bound that spans every
    attempt and every sleep, so the two cannot be confused: raising the
    per-attempt timeout cannot extend the request past the deadline.
    """

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay_seconds: float = DEFAULT_RETRY_BASE_DELAY_SECONDS
    max_delay_seconds: float = DEFAULT_RETRY_MAX_DELAY_SECONDS
    total_deadline_seconds: float = DEFAULT_GENERATION_DEADLINE_SECONDS
    per_attempt_timeout_seconds: float = DEFAULT_ATTEMPT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= HARD_MAX_ATTEMPTS:
            raise ValueError(f"max_attempts must be within [1, {HARD_MAX_ATTEMPTS}], got {self.max_attempts}")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays must not be negative")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must not be below base_delay_seconds")
        if self.total_deadline_seconds <= 0:
            raise ValueError("total_deadline_seconds must be positive")
        if self.per_attempt_timeout_seconds <= 0:
            raise ValueError("per_attempt_timeout_seconds must be positive")

    @classmethod
    def from_env(cls, *, per_attempt_timeout_seconds: float) -> RetryPolicy:
        """Build the policy from environment overrides, each clamped to its bound."""
        return cls(
            max_attempts=_env_int("VLLM_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS, 1, HARD_MAX_ATTEMPTS),
            base_delay_seconds=_env_float(
                "VLLM_RETRY_BASE_DELAY_SECONDS",
                DEFAULT_RETRY_BASE_DELAY_SECONDS,
                0.0,
                5.0,
            ),
            max_delay_seconds=_env_float(
                "VLLM_RETRY_MAX_DELAY_SECONDS",
                DEFAULT_RETRY_MAX_DELAY_SECONDS,
                0.0,
                30.0,
            ),
            total_deadline_seconds=_env_float(
                "VLLM_GENERATION_DEADLINE_SECONDS",
                DEFAULT_GENERATION_DEADLINE_SECONDS,
                0.1,
                600.0,
            ),
            per_attempt_timeout_seconds=per_attempt_timeout_seconds,
        )


def backoff_delay_seconds(
    policy: RetryPolicy,
    *,
    attempts_made: int,
    retry_after_seconds: float | None = None,
) -> float | None:
    """Return how long to wait before the next attempt, or ``None`` for no retry.

    The delay is deterministic exponential backoff capped at
    ``policy.max_delay_seconds``. There is deliberately no random jitter: the
    backoff schedule is part of the tested contract, and an untestable random
    term would be a hole in it. Storm prevention comes from the hard attempt cap,
    the capped delay and the total deadline instead — all three of which are
    deterministic and assertable.

    ``retry_after_seconds`` is the server's own ``Retry-After`` hint on a
    transient status. It raises the delay when it is shorter than the local
    backoff. When it is *longer* than ``max_delay_seconds`` this returns
    ``None``: honouring the server means not retrying inside this policy's
    budget, and retrying earlier than asked is the behaviour that produces a
    storm.
    """
    delay = min(policy.base_delay_seconds * (2 ** (attempts_made - 1)), policy.max_delay_seconds)
    if retry_after_seconds is not None:
        if retry_after_seconds > policy.max_delay_seconds:
            return None
        delay = max(delay, retry_after_seconds)
    return delay


def classify_http_status(status_code: int) -> FailureClass:
    """Classify a non-2xx HTTP status. Only the allow-list is transient."""
    return FailureClass.HTTP_TRANSIENT if status_code in TRANSIENT_HTTP_STATUSES else FailureClass.HTTP_PERMANENT


def classify_transport_exception(exc: BaseException) -> FailureClass:
    """Classify a transport-level exception.

    Ordering matters: ``httpx.ConnectError`` is a ``NetworkError`` and
    ``httpx.PoolTimeout`` is a ``TimeoutException``, so the specific checks come
    first. ``RemoteProtocolError`` is grouped with connection failures because a
    server that closes the socket without a response is the transient case.

    Anything else — a local protocol error, an unsupported scheme, a decoding
    failure, or an exception type this module has never seen — is
    :data:`FailureClass.UNKNOWN` and therefore not retried.
    """
    if isinstance(exc, httpx.TimeoutException):
        return FailureClass.TIMEOUT
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError)):
        return FailureClass.CONNECTION
    return FailureClass.UNKNOWN


def classify_response_exception(exc: BaseException) -> FailureClass:
    """Classify an exception raised while turning a response into a completion."""
    if isinstance(exc, httpx.HTTPStatusError):
        return classify_http_status(exc.response.status_code)
    if isinstance(exc, (ValueError, KeyError, IndexError, TypeError)):
        return FailureClass.MALFORMED_RESPONSE
    return FailureClass.UNKNOWN


def parse_retry_after_seconds(headers) -> float | None:
    """Return a usable numeric ``Retry-After`` value, or ``None``.

    Only the delta-seconds form is honoured. An HTTP-date is ignored rather than
    parsed, so no wall-clock comparison can make the backoff depend on the
    machine's clock.
    """
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


class VLLMGenerationError(GenerationError):
    """A vLLM generation failure with a sanitised, classifiable surface.

    ``__str__`` renders a fixed template. It never interpolates the endpoint
    URL, the response body, or the originating exception's message, because
    those routinely contain credentials and internal hostnames.
    """

    def __init__(
        self,
        failure_class: FailureClass,
        endpoint_key: object,
        *,
        attempts: int,
        status_code: int | None = None,
        elapsed_seconds: float | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.failure_class = FailureClass(failure_class)
        self.endpoint_key = sanitize_endpoint_key(endpoint_key)
        self.attempts = int(attempts)
        self.status_code = None if status_code is None else int(status_code)
        self.elapsed_seconds = None if elapsed_seconds is None else round(float(elapsed_seconds), 3)
        self.retry_after_seconds = retry_after_seconds
        rendered_status = "-" if self.status_code is None else str(self.status_code)
        super().__init__(
            (
                f"vLLM generation failed: class={self.failure_class.value} "
                f"endpoint={self.endpoint_key} status={rendered_status} attempts={self.attempts}"
            ),
            error_code="VLLM_GENERATION_FAILED",
            extra={
                "vllm_failure_class": self.failure_class.value,
                "vllm_endpoint_key": self.endpoint_key,
                "vllm_status_code": self.status_code,
                "vllm_attempts": self.attempts,
            },
        )

    @property
    def retryable(self) -> bool:
        """Whether this failure class is allowed another attempt."""
        return self.failure_class in RETRYABLE_FAILURE_CLASSES

    def __repr__(self) -> str:
        # The default Exception repr would append ``args``, which is already
        # sanitised, but stating it keeps the guarantee explicit and testable.
        return f"VLLMGenerationError({self.failure_class.value!r}, attempts={self.attempts})"


class VLLMMetricsSink:
    """The metric surface the generation path publishes through.

    Every method is a no-op here. ``router/stateless_router.py`` receives a
    concrete sink instead, and the default implementation in
    :mod:`router.stateless_router` publishes onto the existing canonical
    collector. This class exists so a test can assert call counts without
    standing up a metrics collector, and so the contract names the counters it
    depends on in one place.
    """

    def record_attempt(self, *, succeeded: bool, failure_class: str | None) -> None:  # noqa: ARG002
        """Record exactly one HTTP attempt."""

    def record_request(self, *, outcome: str, retries: int, elapsed_ms: float) -> None:  # noqa: ARG002
        """Record exactly one ``route_chat`` call, whatever its outcome."""


#: Terminal outcome of one ``route_chat`` call. ``success`` + ``failed`` +
#: ``budget_exhausted`` always equals the request counter, which is the
#: exactly-once invariant the metrics tests assert.
REQUEST_OUTCOME_SUCCESS = "success"
REQUEST_OUTCOME_FAILED = "failed"
REQUEST_OUTCOME_BUDGET_EXHAUSTED = "budget_exhausted"


class ManualClock:
    """A monotonic clock plus sleeper that only advances when told to.

    Tests drive the deadline and the backoff schedule through this, so budget
    exhaustion and attempt caps are asserted exactly instead of being raced
    against wall time.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        """Account for time an attempt spent without going through :meth:`sleep`."""
        self.now += seconds


#: The production clock pair. Injected rather than referenced directly so a test
#: can substitute a deterministic one without patching module globals.
MONOTONIC_CLOCK: Callable[[], float] = time.monotonic
REAL_SLEEP: Callable[[float], None] = time.sleep
