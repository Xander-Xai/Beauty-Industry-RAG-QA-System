"""Dependency-aware readiness evaluation for traffic admission.

This module exists because ``GET /api/health`` and Kubernetes ``readinessProbe``
answer two different questions, and conflating them is a real operational bug:

* ``GET /api/health`` is a **diagnostic** endpoint. It returns HTTP 200 even when
  every dependency is unreachable, and reports what it saw through
  ``status: healthy | degraded``. That contract is deliberate and unchanged here.
* A readiness probe must **fail closed** when the pod cannot serve the traffic a
  Service would send it. Pointing ``readinessProbe`` at ``/api/health`` makes the
  probe unable to fail on dependency loss, which is why the Kubernetes contract
  previously used it as a "liveness-level gate only".

Why not ``all(dependencies.values())``
--------------------------------------
That would be wrong, and demonstrably so: several dependencies here have real
graceful-degradation paths, so treating them as blockers would pull a Pod that
can still answer queries out of rotation.

The decision table below is derived from the code paths that do the degrading,
not from an assumption that healthy means ready.

======================================  ==========================  ==========
Dependency                              Failure behaviour in code   Readiness
======================================  ==========================  ==========
Redis                                    ``RedisCache`` keeps an L1    degraded
                                         process-local cache and
                                         disables L2 (``_try_connect``
                                         -> ``enabled = False``);
                                         login rate limiting falls
                                         back to in-memory
                                         (``_get_redis_rate_limiter``
                                         returns ``None``).
Elasticsearch (BM25)                      Individually degradable:      degraded
                                         dense Qdrant recall still
                                         returns candidates.
Qdrant (dense)                           Individually degradable: BM25 degraded
                                         recall still returns
                                         candidates.
Qdrant **and** Elasticsearch             ``ParallelRecallManager``
                                         yields no retrieval results,  BLOCKER
                                         so the Evidence Gate rejects
                                         and every query returns a
                                         canned refusal. No serving
                                         capability remains.
MinIO                                    Only ``/api/media/{doc_id}``   degraded
                                         returns 503; query/chat are
                                         unaffected.
gen_4b                                   Serves the ``simple`` and     BLOCKER
                                         ``rewrite`` routing tiers,
                                         which always exist.
gen_14b                                  Serves the ``complex`` tier.  BLOCKER
                                         Reached only in production;   in
                                         non-production ``complex``    production
                                         degrades to ``simple`` by
                                         config, not at runtime.
======================================  ==========================  ==========

The generation row is the one most easily got wrong, so it is stated precisely:

``LLMClient._resolve_endpoint`` (``models/llm_client.py``) downgrades ``gen_14b``
to ``gen_4b`` when ``is_production_mode()`` is false. That is a **routing-config
decision made before the request is sent**, not a retry after a failure. In
production no such downgrade exists: ``LLMClient.generate`` logs and re-raises,
so a dead ``gen_14b`` fails the request outright. A pod that cannot reach the
endpoint its own routing configuration selects is therefore not ready, and
pretending otherwise would ship a Pod that accepts traffic it cannot serve.

Evidence level: REPO_VERIFIED — static reasoning over the code paths above plus
deterministic tests with injected probes. No probe here has been executed
against a real cluster or real vLLM servers; real runtime validation is PENDING.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Per-probe timeout. The Kubernetes readinessProbe allows 5s
#: (``timeoutSeconds: 5``), and all probes run concurrently, so the total wall
#: time stays bounded by roughly this value rather than by their sum.
PROBE_TIMEOUT_SECONDS = 2.0

#: Dependency keys, in a stable order. Used for response determinism and by the
#: manifest/endpoint tests.
DEPENDENCY_KEYS = (
    "redis",
    "qdrant",
    "elasticsearch",
    "minio",
    "gen_4b",
    "gen_14b",
)


@dataclass(frozen=True)
class ReadinessReport:
    """Outcome of one readiness evaluation.

    ``ready`` is the traffic-admission decision. ``degraded`` lists dependencies
    that are down but whose failure the serving contract tolerates, and
    ``blockers`` lists those whose failure means the pod cannot serve. Both lists
    name dependencies only — they never carry URLs, credentials or exception
    text, because this payload is returned to an unauthenticated caller.
    """

    ready: bool
    dependencies: dict[str, bool]
    degraded: list[str]
    blockers: list[str]

    @property
    def status(self) -> str:
        return "ready" if self.ready else "not_ready"

    def to_payload(self) -> dict[str, Any]:
        """Serialize for the HTTP response.

        Field names follow the existing ``HealthResponse`` style: snake_case
        with an explicit ``status`` discriminator.
        """
        return {
            "status": self.status,
            "dependencies": dict(self.dependencies),
            "degraded": list(self.degraded),
            "blockers": list(self.blockers),
        }


@dataclass
class ProbeContext:
    """Everything a probe needs, resolved once per evaluation.

    Probes are injected so tests can drive every branch deterministically without
    a live Redis, Qdrant, Elasticsearch, MinIO or vLLM server.
    """

    is_production: bool
    endpoints: dict[str, str] = field(default_factory=dict)
    qdrant: dict[str, Any] = field(default_factory=dict)
    elasticsearch: dict[str, Any] = field(default_factory=dict)
    redis: dict[str, Any] = field(default_factory=dict)
    minio: dict[str, Any] = field(default_factory=dict)


def required_generation_endpoints(ctx: ProbeContext) -> list[str]:
    """Return the generation endpoints this deployment must be able to serve.

    Mirrors the routing contract rather than assuming the production topology:

    * ``gen_4b`` is always required. ``model_routing.tiers`` maps both
      ``simple`` and ``rewrite`` to it, so every deployment routes there.
    * ``gen_14b`` is required only in production, because
      ``resolve_model_endpoint`` (``common/config.py``) rewrites the ``complex``
      tier to ``simple`` when not in production.

    The tier→endpoint mapping is read from config, so a deployment that re-points
    a tier is evaluated against its own routing table.
    """
    required = ["gen_4b"]
    if ctx.is_production:
        required.append("gen_14b")
    return required


def _resolve_endpoints() -> dict[str, str]:
    """Resolve the vLLM base URLs exactly as ``StatelessRouter`` does.

    ``StatelessRouter.__init__`` reads ``VLLM_4B_URL`` / ``VLLM_GEN_14B_URL``
    from the environment and otherwise derives a localhost URL from the
    configured ports. Readiness must probe the URLs the router will actually
    call, so it reuses that same resolution rather than a second source of
    truth.
    """
    from common.config import get_config_dict

    cfg = get_config_dict()
    vllm_4b_port = cfg["gpu1"]["models"]["vllm_4b"]["port"]
    gen_14b_port = cfg["gpu0"]["models"]["gen_14b"]["port"]
    return {
        "gen_4b": os.environ.get("VLLM_4B_URL", f"http://localhost:{vllm_4b_port}"),
        "gen_14b": os.environ.get("VLLM_GEN_14B_URL", f"http://localhost:{gen_14b_port}"),
    }


def _build_context() -> ProbeContext:
    from common.config import get_config_dict, is_production_mode

    cfg = get_config_dict()
    return ProbeContext(
        is_production=is_production_mode(),
        endpoints=_resolve_endpoints(),
        qdrant=cfg.get("qdrant", {}),
        elasticsearch=cfg.get("elasticsearch", {}),
        redis=cfg.get("redis", {}),
        minio=cfg.get("minio", {}),
    )


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------
#
# Each probe returns a bool and never raises: an unreachable dependency is an
# expected answer, not an endpoint error. Failures are logged at debug level
# with the dependency name only, never with the exception text, because these
# logs sit next to credential-bearing configuration.


def probe_redis(ctx: ProbeContext) -> bool:
    """True when the L2 cache/session store is reachable.

    Only ever contributes to ``degraded``: the L1 process-local cache and the
    in-memory rate limiter both keep working without Redis.
    """
    try:
        from cache.redis_cache import RedisCache

        return bool(RedisCache().enabled)
    except Exception:
        logger.debug("readiness: redis probe failed")
        return False


def probe_qdrant(ctx: ProbeContext) -> bool:
    """True when the dense vector store answers.

    Uses a real API call. ``QdrantClient.healthcheck()`` is deliberately not
    used: it does not exist on ``qdrant-client`` 1.18.0, so calling it raises
    ``AttributeError`` and would report every deployment as Qdrant-down.
    ``get_collections()`` is the lightest call that genuinely proves the
    server is reachable.
    """
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(
            host=ctx.qdrant.get("host", "localhost"),
            port=ctx.qdrant.get("port", 6333),
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        client.get_collections()
        return True
    except Exception:
        logger.debug("readiness: qdrant probe failed")
        return False


def probe_elasticsearch(ctx: ProbeContext) -> bool:
    """True when Elasticsearch answers an authenticated ping.

    Credential resolution must match ``BM25Retriever.es_client`` and the
    ``/api/health`` sub-check (Issue #43), otherwise readiness would inherit the
    same anonymous-client false negative that fix removed.
    """
    try:
        from elasticsearch import Elasticsearch

        es_cfg = ctx.elasticsearch
        kwargs: dict[str, Any] = {"hosts": [es_cfg.get("host", "http://localhost:9200")]}
        username = os.environ.get("ELASTICSEARCH_USERNAME") or es_cfg.get("username", "")
        password = os.environ.get("ELASTICSEARCH_PASSWORD") or es_cfg.get("password", "")
        if username and password:
            kwargs["basic_auth"] = (username, password)
        return bool(Elasticsearch(**kwargs).ping())
    except Exception:
        logger.debug("readiness: elasticsearch probe failed")
        return False


def probe_minio(ctx: ProbeContext) -> bool:
    """True when the object store initializes.

    Never a blocker: only ``/api/media/{doc_id}`` depends on it.
    """
    try:
        from common.minio_client import get_minio_client

        return bool(get_minio_client().is_available)
    except Exception:
        logger.debug("readiness: minio probe failed")
        return False


def probe_generation(endpoint_key: str) -> Callable[[ProbeContext], bool]:
    """Build a probe for one vLLM endpoint.

    Uses the OpenAI-compatible ``GET /v1/models``, which proves the server is
    accepting and can serve completions without generating tokens or consuming
    GPU time. It is also the lightest endpoint that fails when weights are
    absent, which is exactly the state that must not receive traffic.
    """

    def _probe(ctx: ProbeContext) -> bool:
        base_url = ctx.endpoints.get(endpoint_key, "")
        if not base_url:
            return False
        try:
            import httpx

            with httpx.Client(timeout=PROBE_TIMEOUT_SECONDS) as client:
                response = client.get(f"{base_url}/v1/models")
                return response.status_code == 200
        except Exception:
            logger.debug("readiness: generation probe failed for %s", endpoint_key)
            return False

    return _probe


def _stable_order(names: list[str]) -> list[str]:
    """Order ``names`` by DEPENDENCY_KEYS, keeping unknown names first.

    Response payloads should be byte-stable so a test can assert on them and an
    operator sees the same list twice in a row. Known dependencies come first in
    the declared order; anything else (capability names such as ``retrieval``)
    is preserved ahead of them in insertion order rather than dropped.
    """
    known = [name for name in DEPENDENCY_KEYS if name in set(names)]
    extra = [name for name in names if name not in DEPENDENCY_KEYS]
    return extra + known


def _default_probes(ctx: ProbeContext) -> dict[str, Callable[[ProbeContext], bool]]:
    return {
        "redis": probe_redis,
        "qdrant": probe_qdrant,
        "elasticsearch": probe_elasticsearch,
        "minio": probe_minio,
        "gen_4b": probe_generation("gen_4b"),
        "gen_14b": probe_generation("gen_14b"),
    }


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def evaluate_readiness(
    ctx: ProbeContext | None = None,
    probes: dict[str, Callable[[ProbeContext], bool]] | None = None,
) -> ReadinessReport:
    """Evaluate whether this pod can serve the traffic routed to it.

    Ready requires all three of:

    A. the API process answers this call at all (implicit: the handler ran);
    B. **at least one** retrieval path survives — ``qdrant OR elasticsearch``.
       Either alone still returns candidates, so either alone is serving. Only
       the loss of both means no retrieval capability at all;
    C. every generation endpoint this deployment's own routing selects is
       reachable, per :func:`required_generation_endpoints`.

    Redis and MinIO failures land in ``degraded``: their code paths degrade, so
    they must not remove a serving Pod from rotation.

    All probes run concurrently, so the wall time is bounded by the slowest
    probe rather than their sum.
    """
    context = ctx if ctx is not None else _build_context()
    active = probes if probes is not None else _default_probes(context)

    with ThreadPoolExecutor(max_workers=max(len(DEPENDENCY_KEYS), 1)) as executor:
        futures = {key: executor.submit(active[key], context) for key in DEPENDENCY_KEYS if key in active}
        dependencies: dict[str, bool] = {}
        for key in DEPENDENCY_KEYS:
            if key not in futures:
                # A probe that was not supplied is not evidence of health.
                dependencies[key] = False
                continue
            try:
                dependencies[key] = bool(futures[key].result())
            except Exception:
                # evaluate_readiness must not propagate: a broken probe becomes
                # an unhealthy dependency, not a 500 from the probe endpoint.
                logger.debug("readiness: probe %s raised", key)
                dependencies[key] = False

    required_generation = required_generation_endpoints(context)

    blockers: list[str] = []
    degraded: list[str] = []

    # B. Retrieval OR semantics.
    retrieval_available = dependencies["qdrant"] or dependencies["elasticsearch"]
    if not retrieval_available:
        blockers.append("retrieval")

    # C. Generation endpoints required by this deployment's routing.
    for key in required_generation:
        if not dependencies.get(key, False):
            blockers.append(key)

    # Individually-degraded retrieval paths: reported so an operator can see
    # which one survived, without implying the pod is unfit for traffic. If
    # neither survived, "retrieval" is already a blocker and naming the two again
    # would double-report the same outage.
    if retrieval_available:
        for key in ("qdrant", "elasticsearch"):
            if not dependencies[key]:
                degraded.append(key)

    # Report-only dependencies: down is tolerated, so they are never blockers.
    # Redis degradation is what keeps sessions and login rate limiting alive in
    # process; MinIO degradation costs the media route only.
    for key in ("redis", "minio"):
        if not dependencies[key]:
            degraded.append(key)

    # A generation endpoint this deployment does not route to is not consulted
    # for admission. It is surfaced only when it is also unreachable, so the
    # operator sees the state without it blocking traffic: outside production
    # the complex tier degrades to gen_4b by configuration, before the request
    # is sent, so an absent gen_14b is a real fact about the deployment but not
    # a serving failure.
    for key in ("gen_4b", "gen_14b"):
        if key not in required_generation and not dependencies[key]:
            degraded.append(key)

    # Stable order, deduplicated. Dependency names sort by DEPENDENCY_KEYS so the
    # payload is byte-stable across calls; capability names (e.g. "retrieval")
    # are not dependencies and are therefore kept in their own leading position.
    degraded = _stable_order(degraded)
    blockers = _stable_order(blockers)

    return ReadinessReport(
        ready=not blockers,
        dependencies=dependencies,
        degraded=degraded,
        blockers=blockers,
    )
