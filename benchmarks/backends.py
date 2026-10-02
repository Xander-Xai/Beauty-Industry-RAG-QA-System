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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse, urlunsplit

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

    The port is taken from the URL only. ``BM25Retriever`` passes just
    ``hosts=[host]`` to the Elasticsearch client, so a separate ``port`` field in
    ``config.json`` is never used by production. Honouring it here would make the
    TCP pre-check probe a port retrieval never touches, and a closed listener
    there would block a configuration whose real retrieval path works.
    """
    from urllib.parse import urlparse

    from common.config import get_config_dict

    config = get_config_dict()
    elastic = config.get("elasticsearch", {}) or {}
    url = str(elastic.get("host") or "http://elasticsearch:9200")
    index = str(elastic.get("index") or "cosmetics_docs")
    parsed = urlparse(url if "//" in url else f"//{url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 9200)
    auth: tuple[str, str] | None = None
    # Mirror the production BM25Retriever precedence exactly: environment first,
    # config.json only as a fallback, otherwise a rotated credential makes real
    # retrieval succeed while the probe reports a 401.
    # Each credential source is used atomically: a half from one source must never
    # be paired with a half from another, because that would fabricate a principal
    # (for example env username + URL password) that exists in neither place.
    env_user = os.environ.get("ELASTICSEARCH_USERNAME")
    env_pass = os.environ.get("ELASTICSEARCH_PASSWORD")
    config_user = elastic.get("username")
    config_pass = elastic.get("password")
    url_user, url_pass = elastic_url_userinfo(str(elastic.get("host") or ""))
    auth = None
    for candidate_user, candidate_pass in (
        (env_user, env_pass),
        (config_user, config_pass),
        (url_user, url_pass),
    ):
        if candidate_user and candidate_pass:
            auth = (str(candidate_user), str(candidate_pass))
            break
    # The credentials travel in the Authorization header, so the userinfo must be
    # removed from the URL: urllib keeps it as part of the connection host, which
    # breaks DNS/connection setup even though the header is correct.
    return strip_url_userinfo(url), index, port, auth


def strip_url_userinfo(url: str) -> str:
    """Return ``url`` without its ``user:password@`` component."""
    if not url or "@" not in url:
        return url
    try:
        parsed = urlparse(url if "//" in url else f"//{url}")
    except ValueError:
        return url
    if not parsed.netloc or "@" not in parsed.netloc:
        return url
    host = parsed.netloc.rsplit("@", 1)[-1]
    rebuilt = urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
    return rebuilt if "://" in url else f"//{rebuilt}"


def elastic_url_userinfo(host: str) -> tuple[str | None, str | None]:
    """Extract ``(username, password)`` from a URL's userinfo component.

    A URL such as ``https://user:password@host:9200`` is a supported way to
    configure Elasticsearch. urllib does not turn the userinfo into an
    Authorization header, so without this the probe would send the raw
    ``user:password@host`` as the request host and could reject an instance the
    production client authenticates against successfully.
    """
    if not host or "@" not in host:
        return None, None
    try:
        parsed = urlparse(host if "//" in host else f"//{host}")
    except ValueError:
        return None, None
    if not parsed.username:
        return None, None
    # urlparse leaves escapes in place ("p%40ss"), but the semantic credential is
    # "p@ss"; sending the escaped form would authenticate with the wrong password.
    username = unquote(parsed.username)
    password = unquote(parsed.password) if parsed.password is not None else None
    return username, password


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
_RECOGNIZED_WEIGHT_FILENAMES = frozenset({"model.safetensors", "pytorch_model.bin"})
# `AutoTokenizer.from_pretrained` needs an actual vocabulary. `tokenizer_config.json`
# alone carries no tokens and would still fail to load.
_TOKENIZER_VOCAB_NAMES = ("tokenizer.json", "vocab.txt", "spiece.model", "sentencepiece.bpe.model")


def _non_empty(path: Path) -> bool:
    """A zero-byte placeholder from an interrupted download is not an artifact."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


INVALID_WEIGHT_INDEX = "invalid"


def _declared_shards(path: Path) -> list[str] | None | str:
    """Shard filenames declared by a transformers weight index.

    Returns ``None`` when no index exists, :data:`INVALID_WEIGHT_INDEX` when one
    exists but cannot be parsed into a ``weight_map`` (which from_pretrained would
    still fail on), and the shard list otherwise. An unreadable index must not be
    conflated with an absent one, or a partial download looks complete.
    """
    for index_name in ("model.safetensors.index.json", "pytorch_model.bin.index.json"):
        index_file = path / index_name
        if not index_file.is_file():
            continue
        if not _non_empty(index_file):
            # Present but zero-byte: an interrupted download, not "no index".
            # from_pretrained still reads it, so it must not be skipped.
            return INVALID_WEIGHT_INDEX
        try:
            index = json.loads(index_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return INVALID_WEIGHT_INDEX
        weight_map = index.get("weight_map") if isinstance(index, dict) else None
        if isinstance(weight_map, dict) and weight_map:
            return sorted({str(name) for name in weight_map.values()})
        return INVALID_WEIGHT_INDEX
    return None


def _looks_like_model_dir(path: Path) -> tuple[bool, str]:
    if not path.is_dir():
        return False, "path is not a directory"
    missing: list[str] = []
    if not any(_non_empty(path / name) for name in _MODEL_CONFIG_NAMES):
        missing.append("config.json")
    if not any(_non_empty(path / name) for name in _TOKENIZER_VOCAB_NAMES):
        missing.append("tokenizer vocabulary (tokenizer.json/vocab.txt/spiece.model)")
    weight_files = [candidate for suffix in _MODEL_WEIGHT_SUFFIXES for candidate in sorted(path.glob(f"*{suffix}"))]
    weight_files = [candidate for candidate in weight_files if _non_empty(candidate)]
    if not weight_files:
        missing.append("model weights (*.safetensors/*.bin)")
    else:
        # A single shard can be present while the rest of the download is missing,
        # which from_pretrained still rejects; follow the index when there is one.
        # A *.bin wildcard also matches training_args.bin and similar, which
        # from_pretrained does not treat as weights; only the recognized
        # checkpoint names are accepted when no index declares the shards.
        declared = _declared_shards(path)
        if declared == INVALID_WEIGHT_INDEX:
            missing.append("a readable transformers weight index")
        elif declared is not None:
            present = {candidate.name for candidate in weight_files}
            absent = [shard for shard in declared if shard not in present]
            if absent:
                missing.append(f"{len(absent)} of {len(declared)} weight shard(s), e.g. {absent[0]}")
        else:
            recognized = [candidate for candidate in weight_files if candidate.name in _RECOGNIZED_WEIGHT_FILENAMES]
            if not recognized:
                missing.append("model weights (" + "/".join(sorted(_RECOGNIZED_WEIGHT_FILENAMES)) + ")")
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
    # urlparse().hostname handles IPv6 literals ("[::1]") and strips userinfo,
    # both of which a naive string split mangles.
    if not _tcp_reachable(urlparse(url).hostname or "localhost", port):
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
    ok, payload = _http_get(f"http://{host}:{port}/collections/{collection}") if rest_reachable else (False, None)
    # The production client prefers gRPC (QdrantClient(..., prefer_grpc=True)), so
    # gRPC is the decisive transport whenever it is reachable: a working REST port
    # — or a REST 401/403/404 from an unauthenticated proxy — does not prove that
    # real retrieval would succeed.
    rest_points = None
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            rest_points = result.get("points_count")

    if grpc_reachable and grpc_port is not None:
        grpc_points, grpc_note = _grpc_collection_points(host, grpc_port, collection)
        if grpc_points is None:
            return BackendAvailability("dense", False, REASON_SERVICE_NO_DATA, grpc_note)
        if int(grpc_points) == 0:
            return BackendAvailability(
                "dense",
                False,
                REASON_SERVICE_NO_DATA,
                f"collection {collection} exists but holds 0 points over gRPC",
            )
    else:
        # gRPC is not available, so the REST listing is the only evidence there can be.
        if rest_reachable and ok == "http":
            # HTTP-level answer proves reachability; the blocker is the collection.
            return BackendAvailability(
                "dense",
                False,
                REASON_SERVICE_NO_DATA,
                f"collection {collection} answered HTTP {payload} (absent or inaccessible)",
            )
        if rest_points is None:
            return BackendAvailability("dense", False, REASON_SERVICE_UNREACHABLE, f"collection {collection}")
        if int(rest_points) == 0:
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


def probe_config_backends(config_name: str, dataset_queries: int) -> tuple[BackendAvailability, ...]:
    """Probe each backend a configuration requires exactly once.

    The caller reuses this single snapshot for both the availability verdict and
    the manifest. Probing twice lets a service that changes state between the two
    calls produce an artifact whose outcome reason and manifest disagree.
    """
    if config_name not in REQUIRED_BACKENDS:
        return ()
    probed: list[BackendAvailability] = []
    for backend_name in REQUIRED_BACKENDS[config_name]:
        if backend_name == "corpus":
            probed.append(probe_corpus(dataset_queries))
        else:
            probed.append(PROBES[backend_name]())
    return tuple(probed)


def evaluate_config(
    config_name: str,
    dataset_queries: int,
    probed: Sequence[BackendAvailability] | None = None,
) -> ConfigAvailability:
    """Whether a configuration's backends are usable, without executing retrieval.

    ``probed`` reuses an existing probe snapshot instead of probing again.
    """
    if config_name not in REQUIRED_BACKENDS:
        return ConfigAvailability(config_name, False, (f"unknown configuration: {config_name}",), None)
    probed = tuple(probed) if probed is not None else probe_config_backends(config_name, dataset_queries)
    reasons = [f"{item.name}={item.reason}" for item in probed if not item.available]
    details = [f"{item.name}: {item.detail}" for item in probed if not item.available and item.detail]
    return ConfigAvailability(config_name, not reasons, tuple(reasons), "; ".join(details) or None)


def backend_manifest(config_name: str, dataset_queries: int) -> list[dict[str, Any]]:
    """Full probe record for a configuration, including its stages."""
    manifest: list[dict[str, Any]] = []
    for backend_name in REQUIRED_BACKENDS[config_name]:
        availability = probe_corpus(dataset_queries) if backend_name == "corpus" else PROBES[backend_name]()
        manifest.append(availability.as_dict())
    return manifest
