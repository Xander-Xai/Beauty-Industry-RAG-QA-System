"""Data structures shared by the retrieval benchmark harness."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Latency keys that are always reported, even when a stage did not run.
LATENCY_STAGES = (
    "total_retrieval_ms",
    "bm25_ms",
    "dense_ms",
    "rrf_ms",
    "biencoder_ms",
    "crossencoder_ms",
)

RECALL_KS = (1, 3, 5, 10)
HIT_KS = (1, 3, 5, 10)
MRR_K = 10
NDCG_K = 10


@dataclass(frozen=True)
class RelevantItem:
    """One ground-truth passage that a query is expected to retrieve.

    ``key`` is the stable identifier when the dataset exposes one
    (``doc_id`` / ``chunk_id`` / ``source_id``). When no stable identifier is
    available the relevance layer derives a deterministic normalized key from
    ``text`` instead.
    """

    key: str
    text: str


@dataclass(frozen=True)
class RetrievedItem:
    """One item returned by a retrieval backend, already de-duplicated and ranked."""

    rank: int
    key: str
    score: float
    source: str
    text: str = ""


@dataclass(frozen=True)
class BenchmarkQuery:
    """A single benchmark query with its ground truth."""

    sample_id: str
    question: str
    business_type: str
    difficulty: str
    relevant_items: tuple[RelevantItem, ...]
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class BackendAvailability:
    """Why a backend can or cannot be executed.

    ``available`` is only ever ``True`` when a real dependency answered a real
    probe. Benchmark code must not construct an available backend without
    probing it.
    """

    name: str
    available: bool
    reason: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "available": self.available,
            "reason": self.reason,
            "detail": self.detail,
        }


# Configuration outcomes. These four states are intentionally distinct and must
# never be conflated: IMPLEMENTED is about code, EXECUTED is about a measured
# artifact, SKIPPED is a deliberate user choice, BLOCKED is an unavailable
# dependency.
STATUS_IMPLEMENTED = "IMPLEMENTED"
STATUS_EXECUTED = "EXECUTED"
STATUS_SKIPPED = "SKIPPED"
STATUS_BLOCKED = "BLOCKED"
STATUS_NOT_EXECUTED = "NOT_EXECUTED"


@dataclass(frozen=True)
class ConfigOutcome:
    """Execution status for one benchmark configuration."""

    config_name: str
    status: str
    reason: str = ""
    backend_availability: tuple[BackendAvailability, ...] = ()

    @property
    def produced_results(self) -> bool:
        return self.status == STATUS_EXECUTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "config": self.config_name,
            "status": self.status,
            "reason": self.reason,
            "backends": [item.as_dict() for item in self.backend_availability],
        }


@dataclass
class QueryBenchmarkResult:
    """Per-query metrics plus per-stage latency for one configuration."""

    sample_id: str
    question: str
    business_type: str
    difficulty: str
    config: str
    relevant_keys: tuple[str, ...]
    retrieved_keys: tuple[str, ...]
    first_relevant_rank: int | None
    recall_at: dict[int, float | None]
    hit_at: dict[int, bool | None]
    reciprocal_rank: float | None
    ndcg_at_10: float | None
    latency_ms: dict[str, float | None]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "question": self.question,
            "business_type": self.business_type,
            "difficulty": self.difficulty,
            "config": self.config,
            "relevant_items": list(self.relevant_keys),
            "retrieved_items": list(self.retrieved_keys),
            "first_relevant_rank": self.first_relevant_rank,
            "hit_at_1": self.hit_at.get(1),
            "hit_at_3": self.hit_at.get(3),
            "hit_at_5": self.hit_at.get(5),
            "hit_at_10": self.hit_at.get(10),
            "recall_at_1": self.recall_at.get(1),
            "recall_at_3": self.recall_at.get(3),
            "recall_at_5": self.recall_at.get(5),
            "recall_at_10": self.recall_at.get(10),
            "rr": self.reciprocal_rank,
            "ndcg_at_10": self.ndcg_at_10,
            "latency_ms": dict(self.latency_ms),
        }


@dataclass
class BenchmarkRunMetadata:
    """Provenance for one benchmark run."""

    run_id: str
    timestamp_utc: str
    git_sha: str | None
    git_dirty: bool | None
    dataset_path: str | None
    dataset_sha256: str | None
    sample_count: int
    sample_ids_hash: str | None
    config_name: str
    config_sha256: str | None
    requested_configs: list[str] = field(default_factory=list)
    allow_dirty: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(self.extra)
        return payload
