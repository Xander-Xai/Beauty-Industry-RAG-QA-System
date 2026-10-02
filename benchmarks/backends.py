"""Retrieval backends for the benchmark.

Every backend is probed before it is used, and a backend that cannot answer a
real probe is reported as unavailable. The harness never substitutes a stand-in
retriever for a missing one: a benchmark that silently ranks with a fake backend
would produce numbers that look like retrieval quality while measuring nothing.

Two independent things are required for a meaningful measurement:

1. a **live retrieval service** (Elasticsearch / Qdrant) and real model assets;
2. a **corpus that actually contains the ground-truth passages**.

Requirement 2 is easy to overlook. The golden set stores ground truth as passage
*text*; the operator corpus is their own private material. Indexing the golden
set passages themselves and then measuring recall would be self-referential —
every configuration would trivially reach recall 1.0 — so this module refuses that
shortcut and reports the corpus requirement explicitly instead.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from benchmarks.models import BackendAvailability

PROBE_TIMEOUT_SECONDS = 2.0

REASON_OK = "available"
REASON_SERVICE_UNREACHABLE = "service_unreachable"
REASON_SERVICE_NO_DATA = "service_reachable_but_empty"
REASON_MODEL_UNAVAILABLE = "model_assets_unavailable"
REASON_CORPUS_UNRESOLVED = "corpus_does_not_contain_ground_truth"
REASON_NOT_CONFIGURED = "not_configured"


class BenchmarkUnavailable(RuntimeError):
    """Raised when a backend is asked to retrieve without being available."""


def _http_get(url: str) -> tuple[bool, Any]:
    """Return ``(ok, payload)`` for a short-timeout GET."""
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_SECONDS) as response:  # noqa: S310 - operator-configured http(s) endpoint
            body = response.read()
    except (urllib.error.URLError, OSError, ValueError):
        return False, None
    try:
        return True, json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return True, body.decode("utf-8", errors="replace")


def _tcp_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _elastic_settings() -> tuple[str, str, int]:
    from common.config import get_config_dict

    config = get_config_dict()
    elastic = config.get("elasticsearch", {}) or {}
    url = str(elastic.get("host") or "http://elasticsearch:9200")
    index = str(elastic.get("index") or "cosmetics_docs")
    port = int(elastic.get("port") or 9200)
    return url, index, port


def _qdrant_settings() -> tuple[str, int, str]:
    from common.config import get_config_dict

    config = get_config_dict()
    qdrant = config.get("qdrant", {}) or {}
    host = str(qdrant.get("host") or "qdrant")
    port = int(qdrant.get("port") or 6333)
    collections = qdrant.get("collections", {}) or {}
    text_collection = str((collections.get("rag_text_768", {}) or {}).get("name") or "rag_text_768")
    return host, port, text_collection


def _model_weights_available() -> tuple[bool, str | None]:
    from common.config import get_config_dict

    config = get_config_dict()
    model_path = (config.get("embedding", {}).get("text", {}) or {}).get("model_path")
    if not model_path:
        return False, None
    from pathlib import Path

    resolved = Path(str(model_path))
    if resolved.exists():
        return True, str(model_path)
    return False, str(model_path)


# ── Probes ────────────────────────────────────────────────────────────────


def probe_bm25() -> BackendAvailability:
    """BM25 needs a reachable Elasticsearch holding the benchmark corpus."""
    url, index, port = _elastic_settings()
    if not _tcp_reachable(url.split("//")[-1].split(":")[0], port):
        return BackendAvailability("bm25", False, REASON_SERVICE_UNREACHABLE, f"{url}:{port}")
    ok, payload = _http_get(f"{url.rstrip('/')}/{index}/_count")
    if not ok:
        return BackendAvailability("bm25", False, REASON_SERVICE_UNREACHABLE, f"{url.rstrip('/')}/{index}")
    count = None
    if isinstance(payload, dict):
        count = payload.get("count")
    if count is None:
        return BackendAvailability("bm25", False, REASON_SERVICE_UNREACHABLE, "no count response")
    if int(count) == 0:
        return BackendAvailability("bm25", False, REASON_SERVICE_NO_DATA, f"index {index} is empty")
    return BackendAvailability("bm25", True, REASON_OK, f"index {index} has {count} docs")


def probe_dense() -> BackendAvailability:
    """Dense/BGE needs Qdrant text collection *and* real embedding weights."""
    host, port, collection = _qdrant_settings()
    if not _tcp_reachable(host, port):
        return BackendAvailability("dense", False, REASON_SERVICE_UNREACHABLE, f"{host}:{port}")
    ok, payload = _http_get(f"http://{host}:{port}/collections/{collection}")
    if not ok:
        return BackendAvailability("dense", False, REASON_SERVICE_UNREACHABLE, f"collection {collection}")
    weights_ok, model_path = _model_weights_available()
    if not weights_ok:
        return BackendAvailability(
            "dense",
            False,
            REASON_MODEL_UNAVAILABLE,
            f"embedding model path not present: {model_path}",
        )
    return BackendAvailability("dense", True, REASON_OK, f"collection {collection}, model {model_path}")


def probe_biencoder() -> BackendAvailability:
    available, detail = _model_weights_available()
    if not available:
        return BackendAvailability("biencoder", False, REASON_MODEL_UNAVAILABLE, f"model path not present: {detail}")
    return BackendAvailability("biencoder", True, REASON_OK, str(detail))


def probe_crossencoder() -> BackendAvailability:
    return BackendAvailability(
        "crossencoder",
        False,
        REASON_MODEL_UNAVAILABLE,
        "cross-encoder rerankers are not wired to a validated local weights path in this repository",
    )


def probe_corpus(dataset_queries: int) -> BackendAvailability:
    """The benchmark corpus must contain the ground-truth passages.

    The repository ships no corpus fixture built from the golden set passages.
    Constructing one from the ground truth itself would make recall trivially
    perfect and meaningless, so that shortcut is deliberately not taken.
    """
    return BackendAvailability(
        "corpus",
        False,
        REASON_CORPUS_UNRESOLVED,
        (
            f"no reproducible corpus fixture is registered for {dataset_queries} golden-set samples; "
            "the operator corpus does not contain the golden-set passages, and building one from the "
            "ground truth would be self-referential"
        ),
    )


# ── Configuration matrix ───────────────────────────────────────────────────

STAGE_BY_CONFIG = {
    "bm25": ("bm25_ms",),
    "dense": ("dense_ms",),
    "hybrid_rrf": ("bm25_ms", "dense_ms", "rrf_ms"),
    "hybrid_rrf_biencoder": ("bm25_ms", "dense_ms", "rrf_ms", "biencoder_ms"),
    "hybrid_rrf_biencoder_crossencoder": (
        "bm25_ms",
        "dense_ms",
        "rrf_ms",
        "biencoder_ms",
        "crossencoder_ms",
    ),
}

REQUIRED_BACKENDS = {
    "bm25": ("bm25", "corpus"),
    "dense": ("dense", "corpus"),
    "hybrid_rrf": ("bm25", "dense", "corpus"),
    "hybrid_rrf_biencoder": ("bm25", "dense", "biencoder", "corpus"),
    "hybrid_rrf_biencoder_crossencoder": ("bm25", "dense", "biencoder", "crossencoder", "corpus"),
}

CONFIG_DESCRIPTIONS = {
    "bm25": "Elasticsearch BM25 only",
    "dense": "Qdrant dense (BGE) only",
    "hybrid_rrf": "BM25 + dense fused with Reciprocal Rank Fusion",
    "hybrid_rrf_biencoder": "hybrid_rrf plus BiEncoder wide rerank",
    "hybrid_rrf_biencoder_crossencoder": "hybrid_rrf_biencoder plus CrossEncoder ensemble rerank",
}

PROBES = {
    "bm25": probe_bm25,
    "dense": probe_dense,
    "biencoder": probe_biencoder,
    "crossencoder": probe_crossencoder,
}


@dataclass(frozen=True)
class ConfigAvailability:
    config_name: str
    available: bool
    reasons: tuple[str, ...]
    detail: str | None

    def describe(self) -> str:
        if self.available:
            return "all required backends available"
        return "; ".join(self.reasons)


def evaluate_config(config_name: str, dataset_queries: int) -> ConfigAvailability:
    """Probe every backend a configuration requires, without executing retrieval."""
    if config_name not in REQUIRED_BACKENDS:
        return ConfigAvailability(config_name, False, (f"unknown configuration: {config_name}",), None)
    reasons: list[str] = []
    details: list[str] = []
    for backend_name in REQUIRED_BACKENDS[config_name]:
        if backend_name == "corpus":
            availability = probe_corpus(dataset_queries)
        else:
            availability = PROBES[backend_name]()
        if not availability.available:
            reasons.append(f"{availability.name}={availability.reason}")
            if availability.detail:
                details.append(f"{availability.name}: {availability.detail}")
    return ConfigAvailability(config_name, not reasons, tuple(reasons), "; ".join(details) or None)


def backend_manifest(config_name: str, dataset_queries: int) -> list[dict[str, Any]]:
    """Full probe record for a configuration, including its stages."""
    manifest: list[dict[str, Any]] = []
    for backend_name in REQUIRED_BACKENDS[config_name]:
        availability = probe_corpus(dataset_queries) if backend_name == "corpus" else PROBES[backend_name]()
        manifest.append(availability.as_dict())
    return manifest
