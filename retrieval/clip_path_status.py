"""Structured execution status for the CLIP visual recall path.

``_recall_clip`` swallows per-path exceptions and returns ``[]``. That is the right
behaviour for one recall channel degrading — the request must still be answered from
the other paths — but an empty list is also the *correct* answer when the visual
collection genuinely has no match for the query. Returning ``[]`` for both makes a real
defect indistinguishable from a healthy zero-hit recall, which is how an unconditional
``AttributeError`` in the encoder stayed invisible.

This module is the closed vocabulary that separates them. It mirrors
``router.vllm_resilience.FailureClass``: a plain ``str`` Enum whose *values* are the
stable metric tokens, so renaming a member is a metric-name change and not a silent
reclassification.

Only a closed set is allowed, and ``detail`` is a short operator-facing token, never a
query string, a document id or a raw exception message — these values reach logs and
Prometheus series names.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ClipPathOutcome(str, Enum):
    """The closed set of ways the CLIP recall path can terminate.

    The distinction that matters most: :attr:`EMPTY` is a *success*. The path ran, the
    encoder produced a vector, Qdrant answered, and nothing matched. Every other
    non-``OK`` member is a failure of the path rather than of the data.
    """

    #: The path ran and returned at least one hit.
    OK = "ok"
    #: The path ran to completion and the visual collection had no match. Not a failure.
    EMPTY = "empty"
    #: The CLIP processor or weights could not be loaded (missing dir, absent model).
    MODEL_UNAVAILABLE = "model_unavailable"
    #: The encoder raised for some reason other than loading.
    ENCODE_FAILED = "encode_failed"
    #: The Qdrant image-collection query raised.
    QDRANT_FAILED = "qdrant_failed"
    #: The query vector and the collection's configured dimension disagree.
    DIMENSION_MISMATCH = "dimension_mismatch"
    #: The attempt exceeded ``clip_sync.timeout_ms``.
    TIMEOUT = "timeout"
    #: A previous CLIP attempt was still in flight, so this one was not started.
    BUSY = "busy"
    #: Instrumentation is absent (the path was substituted in a test or by a caller).
    UNSET = "unset"
    #: An unrecognised exception. Never silently reported as a healthy empty result.
    UNKNOWN = "unknown"

    @property
    def is_failure(self) -> bool:
        """Whether this outcome means the path degraded rather than returned nothing."""
        return self not in (ClipPathOutcome.OK, ClipPathOutcome.EMPTY, ClipPathOutcome.UNSET)


@dataclass(frozen=True)
class ClipPathStatus:
    """Terminal state of exactly one CLIP path attempt.

    ``detail`` is a bounded operator-facing token (for example ``missing_weights`` or
    ``qdrant_unreachable``). It deliberately carries no query text, no document id and
    no exception message, so it is safe to place in a log line or a metric name.
    """

    outcome: ClipPathOutcome = ClipPathOutcome.UNSET
    hit_count: int = 0
    elapsed_ms: float = 0.0
    detail: str = ""

    def __post_init__(self):
        if self.hit_count < 0:
            raise ValueError("hit_count must not be negative")

    @property
    def is_failure(self) -> bool:
        return self.outcome.is_failure

    def series_suffix(self) -> str:
        """Metric name suffix.

        ``to_prometheus_text`` has no label plumbing, so the classification travels as a
        name suffix — the same shape ``record_vllm_generation_attempt`` already uses.
        The detail token is included only for failures, so a successful path never
        produces an unbounded set of series names.
        """
        base = f"clip.path.outcome.{self.outcome.value}"
        if self.detail and self.is_failure:
            return f"{base}.{self.detail}"
        return base


#: The only ``detail`` tokens a model-unavailable status may carry. A closed set is
#: what keeps the metric vocabulary bounded: an arbitrary exception message would make
#: the series name attacker- and environment-influenced.
MODEL_UNAVAILABLE_DETAILS = frozenset({"missing_weights", "loader_error", "not_installed"})

#: ``detail`` tokens for the remaining failure outcomes.
QDRANT_FAILED_DETAILS = frozenset({"qdrant_unreachable", "collection_missing", "qdrant_error"})
DIMENSION_MISMATCH_DETAILS = frozenset({"query_vs_collection", "query_vs_centroid"})
ENCODE_FAILED_DETAILS = frozenset({"encoder_error"})
