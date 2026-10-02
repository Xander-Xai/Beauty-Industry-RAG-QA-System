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


# A local model directory is only usable when it actually holds transformers
# weights. An empty directory, an unrelated file or an interrupted download must
# not be reported as available. `AutoModel.from_pretrained` needs config *and*
# weights, and `AutoTokenizer.from_pretrained` needs tokenizer assets, so all three
# groups must be present for a partial or interrupted cache to stay unavailable.
_MODEL_CONFIG_NAMES = ("config.json",)
_MODEL_WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".onnx")
_TOKENIZER_ASSET_NAMES = ("tokenizer.json", "tokenizer_config.json", "vocab.txt")


def _looks_like_model_dir(path: Path) -> tuple[bool, str]:
    if not path.is_dir():
        return False, "path is not a directory"
    missing: list[str] = []
    if not any((path / name).is_file() for name in _MODEL_CONFIG_NAMES):
        missing.append("config.json")
    if not any(next(path.glob(f"*{suffix}"), None) is not None for suffix in _MODEL_WEIGHT_SUFFIXES):
        missing.append("model weights (*.safetensors/*.bin/*.pt/*.onnx)")
    if not any((path / name).is_file() for name in _TOKENIZER_ASSET_NAMES):
        missing.append("tokenizer assets (tokenizer.json/tokenizer_config.json/vocab.txt)")
    if missing:
        return False, "incomplete model directory, missing: " + ", ".join(missing)
    return True, "found model config, weights and tokenizer assets"


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
    if not _tcp_reachable(host, port):
        return BackendAvailability("dense", False, REASON_SERVICE_UNREACHABLE, f"{host}:{port}")
    ok, payload = _http_get(f"http://{host}:{port}/collections/{collection}")
    if ok == "http":
        # HTTP-level answer proves reachability; the blocker is the collection.
        return BackendAvailability(
            "dense",
            False,
            REASON_SERVICE_NO_DATA,
            f"collection {collection} answered HTTP {payload} (absent or inaccessible)",
        )
    if not ok:
        return BackendAvailability("dense", False, REASON_SERVICE_UNREACHABLE, f"collection {collection}")
    points = None
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            points = result.get("points_count")
    if points is not None and int(points) == 0:
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
