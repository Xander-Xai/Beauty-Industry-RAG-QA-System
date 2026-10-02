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

import base64
import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.models import BackendAvailability

PROBE_TIMEOUT_SECONDS = 2.0

REASON_OK = "available"
REASON_SERVICE_UNREACHABLE = "service_unreachable"
REASON_SERVICE_NO_DATA = "service_reachable_but_empty"
REASON_MODEL_UNAVAILABLE = "model_assets_unavailable"
REASON_CORPUS_UNRESOLVED = "corpus_does_not_contain_ground_truth"
REASON_NOT_CONFIGURED = "not_configured"
REASON_SERVICE_DISABLED = "service_disabled_by_config"


class BenchmarkUnavailable(RuntimeError):
    """Raised when a backend is asked to retrieve without being available."""


def _http_get(url: str, auth: tuple[str, str] | None = None) -> tuple[bool, Any]:
    """Return ``(ok, payload)`` for a short-timeout GET.

    Compose enables ``xpack.security.enabled``, so the probe must authenticate
    exactly like the production retriever; otherwise a healthy secured
    Elasticsearch answers 401 and would be misreported as unreachable.
    """
    if not url.lower().startswith(("http://", "https://")):
        # Only operator-configured http(s) endpoints are probed; never file:// or
        # a custom scheme, so this can never read a local path.
        return False, None
    request = urllib.request.Request(url)  # noqa: S310 - scheme validated above
    if auth and auth[0]:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:  # noqa: S310
            body = response.read()
    except urllib.error.HTTPError as exc:
        # An HTTP-level answer (401/403/404) proves the service is reachable.
        return "http", exc.code
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


def _elastic_settings() -> tuple[str, str, int, tuple[str, str] | None]:
    """Return ``(url, index, port, auth)`` derived from the live configuration.

    The port comes from the URL when it embeds one, unless the configuration
    overrides it, so ``http://localhost:19200`` is probed on 19200 rather than on
    the 9200 default.
    """
    from urllib.parse import urlparse

    from common.config import get_config_dict

    config = get_config_dict()
    elastic = config.get("elasticsearch", {}) or {}
    url = str(elastic.get("host") or "http://elasticsearch:9200")
    index = str(elastic.get("index") or "cosmetics_docs")
    parsed = urlparse(url if "//" in url else f"//{url}")
    port = int(elastic.get("port") or parsed.port or (443 if parsed.scheme == "https" else 9200))
    auth: tuple[str, str] | None = None
    # Mirror the production BM25Retriever precedence exactly: environment first,
    # config.json only as a fallback, otherwise a rotated credential makes real
    # retrieval succeed while the probe reports a 401.
    username = os.environ.get("ELASTICSEARCH_USERNAME") or elastic.get("username") or ""
    password = os.environ.get("ELASTICSEARCH_PASSWORD") or elastic.get("password") or ""
    if username and password:
        auth = (str(username), str(password))
    return url, index, port, auth


def _qdrant_settings() -> tuple[str, int, str]:
    """Return ``(host, port, text_collection)`` using the configured collection."""
    from common.config import get_config_dict

    config = get_config_dict()
    qdrant = config.get("qdrant", {}) or {}
    host = str(qdrant.get("host") or "qdrant")
    port = int(qdrant.get("port") or 6333)
    configured = (config.get("embedding", {}).get("text", {}) or {}).get("collection")
    text_collection = str(
        configured or (qdrant.get("collections", {}) or {}).get("rag_text_768", {}).get("name") or "rag_text_768"
    )
    return host, port, text_collection


def _grpc_collection_points(host: str, grpc_port: int, collection: str) -> tuple[int | None, str]:
    """Query a collection over gRPC, the transport production retrieval prefers.

    Returns ``(points_count, note)``. ``points_count`` is ``None`` when the
    collection's existence or size could not be established, which must never be
    read as "available".
    """
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        return None, "qdrant_client is not installed, cannot verify the collection over gRPC"
    try:
        client = QdrantClient(
            host=host, port=DEFAULT_QDRANT_REST_PORT, grpc_port=grpc_port, prefer_grpc=True, timeout=10
        )
    except Exception as exc:  # noqa: BLE001 - client construction raises backend-specific errors
        return None, f"gRPC client could not be created ({type(exc).__name__})"
    try:
        info = client.get_collection(collection)
    except Exception as exc:  # noqa: BLE001 - missing/inaccessible collections raise client errors
        return None, f"collection {collection} is absent or inaccessible over gRPC ({type(exc).__name__})"
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    points = getattr(info, "points_count", None)
    if points is None and isinstance(info, dict):
        points = info.get("points_count")
    if points is None:
        return None, f"collection {collection} returned no points_count over gRPC"
    try:
        return int(points), f"collection {collection} verified over gRPC"
    except (TypeError, ValueError):
        return None, f"collection {collection} returned a non-numeric points_count"


DEFAULT_QDRANT_REST_PORT = 6333


def _qdrant_grpc_port() -> int | None:
    """gRPC port used by the production ``EmbeddingService`` client.

    ``EmbeddingService`` builds ``QdrantClient(..., grpc_port=...,
    prefer_grpc=True)``, so gRPC is a first-class production protocol here and a
    REST-only reachability check would misreport dense retrieval as unavailable.
    """
    from common.config import get_config_dict

    grpc_port = (get_config_dict().get("qdrant", {}) or {}).get("grpc_port")
    if grpc_port in (None, ""):
        return None
    try:
        return int(grpc_port)
    except (TypeError, ValueError):
        return None


# A local model directory is only usable when it actually holds transformers
# weights. An empty directory, an unrelated file or an interrupted download must
# not be reported as available. `AutoModel.from_pretrained` needs config *and*
# weights, and `AutoTokenizer.from_pretrained` needs tokenizer assets, so all three
# groups must be present for a partial or interrupted cache to stay unavailable.
_MODEL_CONFIG_NAMES = ("config.json",)
# `AutoModel.from_pretrained` only consumes safetensors or a torch pickle; a bare
# `.pt`/`.onnx` file is not a loadable checkpoint for it.
_MODEL_WEIGHT_SUFFIXES = (".safetensors", ".bin")
# `AutoTokenizer.from_pretrained` needs an actual vocabulary. `tokenizer_config.json`
# alone carries no tokens and would still fail to load.
_TOKENIZER_VOCAB_NAMES = ("tokenizer.json", "vocab.txt")


def _looks_like_model_dir(path: Path) -> tuple[bool, str]:
    if not path.is_dir():
        return False, "path is not a directory"
    missing: list[str] = []
    if not any((path / name).is_file() for name in _MODEL_CONFIG_NAMES):
        missing.append("config.json")
    if not any(next(path.glob(f"*{suffix}"), None) is not None for suffix in _MODEL_WEIGHT_SUFFIXES):
        missing.append("model weights (*.safetensors/*.bin)")
    if not any((path / name).is_file() for name in _TOKENIZER_VOCAB_NAMES):
        missing.append("tokenizer vocabulary (tokenizer.json/vocab.txt)")
    if missing:
        return False, "incomplete model directory, missing: " + ", ".join(missing)
    return True, "found model config, weights and tokenizer vocabulary"


def _model_weights_available() -> tuple[bool, str | None]:
    from common.config import get_config_dict

    config = get_config_dict()
    model_path = (config.get("embedding", {}).get("text", {}) or {}).get("model_path")
    if not model_path:
        return False, None
    resolved = Path(str(model_path))
    ok, detail = _looks_like_model_dir(resolved)
    if not ok:
        return False, f"{model_path} ({detail})"
    return True, f"{model_path} ({detail})"


# ── Probes ────────────────────────────────────────────────────────────────


def probe_bm25() -> BackendAvailability:
    """BM25 needs a reachable, authenticated Elasticsearch holding the corpus."""
    url, index, port, auth = _elastic_settings()
    from common.config import get_config_dict

    es_config = get_config_dict().get("elasticsearch", {}) or {}
    # The production BM25Retriever returns no results while this flag is false,
    # so an unreachable-index probe would disagree with the real executor.
    if not es_config.get("enabled", True):
        return BackendAvailability(
            "bm25",
            False,
            REASON_SERVICE_DISABLED,
            "elasticsearch.enabled is false; BM25Retriever returns no results",
        )
    if not _tcp_reachable(url.split("//")[-1].split(":")[0], port):
        return BackendAvailability("bm25", False, REASON_SERVICE_UNREACHABLE, f"{url}:{port}")
    ok, payload = _http_get(f"{url.rstrip('/')}/{index}/_count", auth=auth)
    if ok == "http":
        # The service answered; a non-200 status is auth/index, not unreachability.
        return BackendAvailability(
            "bm25",
            False,
            REASON_SERVICE_NO_DATA,
            f"index {index} answered HTTP {payload} (auth or index problem, service reachable)",
        )
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
    grpc_port = _qdrant_grpc_port()
    rest_reachable = _tcp_reachable(host, port)
    grpc_reachable = grpc_port is not None and _tcp_reachable(host, grpc_port)
    if not rest_reachable and not grpc_reachable:
        probed = f"{host}:{port}"
        if grpc_port is not None:
            probed += f" (rest) / {host}:{grpc_port} (grpc)"
        return BackendAvailability("dense", False, REASON_SERVICE_UNREACHABLE, probed)
    # The production client prefers gRPC, so a reachable gRPC endpoint is enough
    # to attempt retrieval; the HTTP collection listing stays as the cheap probe
    # when REST is the only thing answering.
    ok, payload = _http_get(f"http://{host}:{port}/collections/{collection}") if rest_reachable else (False, None)
    if rest_reachable and ok == "http":
        # HTTP-level answer proves reachability; the blocker is the collection.
        return BackendAvailability(
            "dense",
            False,
            REASON_SERVICE_NO_DATA,
            f"collection {collection} answered HTTP {payload} (absent or inaccessible)",
        )
    points = None
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            points = result.get("points_count")
    # Without a collection listing, the collection's existence and point count
    # cannot be confirmed over REST, so verify it over gRPC — the transport
    # production retrieval actually uses — before declaring availability.
    if points is None:
        if not grpc_reachable or grpc_port is None:
            return BackendAvailability("dense", False, REASON_SERVICE_UNREACHABLE, f"collection {collection}")
        grpc_points, grpc_note = _grpc_collection_points(host, grpc_port, collection)
        if grpc_points is None:
            return BackendAvailability("dense", False, REASON_SERVICE_NO_DATA, grpc_note)
        points = grpc_points
    if int(points) == 0:
        # An existing but empty collection cannot answer any retrieval query.
        return BackendAvailability(
            "dense",
            False,
            REASON_SERVICE_NO_DATA,
            f"collection {collection} exists but holds 0 points",
        )
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
