"""Deterministic tests for the dependency-aware readiness contract.

``GET /api/ready`` is the traffic-admission contract. ``GET /api/health`` is the
diagnostic contract and must not change. These tests pin both, plus the
deployment-mode-dependent generation semantics that make readiness more than
``all(dependencies)``.

Every probe is injected, so no Redis, Qdrant, Elasticsearch, MinIO or vLLM
server is contacted. Nothing here is runtime evidence: the strongest claim these
tests support is REPO_VERIFIED (static, deterministic), and real cluster
deployment remains PENDING.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Probe harness
# ---------------------------------------------------------------------------

from api.readiness import (  # noqa: E402
    DEPENDENCY_KEYS,
    ProbeContext,
    ReadinessReport,
    evaluate_readiness,
    probe_generation,
    required_generation_endpoints,
)

ALL_UP = dict.fromkeys(DEPENDENCY_KEYS, True)


def _probes(state: dict[str, bool]) -> dict:
    """Build a probe map returning the scripted outcome for each dependency."""

    def make(value: bool):
        def _probe(_ctx):
            return value

        return _probe

    return {key: make(state.get(key, False)) for key in DEPENDENCY_KEYS}


def _evaluate(down: tuple[str, ...] = (), *, is_production: bool = True) -> ReadinessReport:
    state = dict(ALL_UP)
    for key in down:
        state[key] = False
    return evaluate_readiness(ProbeContext(is_production=is_production), _probes(state))


# ---------------------------------------------------------------------------
# 1-3. Retrieval OR semantics: either path alone still serves.
# ---------------------------------------------------------------------------


def test_all_dependencies_healthy_is_ready() -> None:
    report = _evaluate()
    assert report.ready is True
    assert report.blockers == []
    assert report.degraded == []
    assert report.status == "ready"


def test_qdrant_down_elasticsearch_up_stays_ready() -> None:
    """BM25 recall still returns candidates, so the pod can serve."""
    report = _evaluate(("qdrant",))
    assert report.ready is True
    assert report.degraded == ["qdrant"]
    assert report.blockers == []


def test_elasticsearch_down_qdrant_up_stays_ready() -> None:
    """Dense recall still returns candidates, so the pod can serve."""
    report = _evaluate(("elasticsearch",))
    assert report.ready is True
    assert report.degraded == ["elasticsearch"]
    assert report.blockers == []


# ---------------------------------------------------------------------------
# 4. Both retrieval paths gone is the one retrieval blocker.
# ---------------------------------------------------------------------------


def test_both_retrieval_paths_down_is_not_ready() -> None:
    """With neither path the pipeline recalls nothing and every query is refused."""
    report = _evaluate(("qdrant", "elasticsearch"))
    assert report.ready is False
    assert report.status == "not_ready"
    assert "retrieval" in report.blockers


# ---------------------------------------------------------------------------
# 5-6. Degradable dependencies must not remove a serving pod from rotation.
# ---------------------------------------------------------------------------


def test_redis_down_is_degraded_not_blocking() -> None:
    """RedisCache keeps an L1 process-local cache; login rate limiting is in-memory."""
    report = _evaluate(("redis",))
    assert report.ready is True
    assert report.degraded == ["redis"]
    assert "redis" not in report.blockers


def test_minio_down_does_not_fail_query_readiness() -> None:
    """Only /api/media/{doc_id} depends on MinIO."""
    report = _evaluate(("minio",))
    assert report.ready is True
    assert report.degraded == ["minio"]
    assert "minio" not in report.blockers


def test_redis_and_minio_down_together_are_still_ready() -> None:
    report = _evaluate(("redis", "minio"))
    assert report.ready is True
    assert report.blockers == []
    assert report.degraded == ["redis", "minio"]


# ---------------------------------------------------------------------------
# 7-10. Generation readiness follows the deployment's own routing.
# ---------------------------------------------------------------------------


def test_gen_4b_down_is_not_ready_in_production() -> None:
    """`simple` and `rewrite` both route to gen_4b in every deployment."""
    report = _evaluate(("gen_4b",), is_production=True)
    assert report.ready is False
    assert "gen_4b" in report.blockers


def test_gen_14b_down_is_not_ready_in_production() -> None:
    """Production routes `complex` to gen_14b and has no runtime fallback to 4B.

    ``LLMClient._resolve_endpoint`` only downgrades gen_14b outside production,
    and that downgrade happens before the request is sent. ``generate`` re-raises
    on failure, so a dead gen_14b fails requests outright in production.
    """
    report = _evaluate(("gen_14b",), is_production=True)
    assert report.ready is False
    assert "gen_14b" in report.blockers


def test_gen_14b_down_is_ready_outside_production() -> None:
    """Non-production degrades the complex tier to gen_4b by config, not at runtime."""
    report = _evaluate(("gen_14b",), is_production=False)
    assert report.ready is True
    assert "gen_14b" not in report.blockers
    assert "gen_14b" in report.degraded


def test_gen_4b_down_is_not_ready_outside_production() -> None:
    """The test contract must follow config, not a hardcoded production topology."""
    report = _evaluate(("gen_4b",), is_production=False)
    assert report.ready is False
    assert "gen_4b" in report.blockers


def test_required_generation_endpoints_follow_deployment_mode() -> None:
    assert required_generation_endpoints(ProbeContext(is_production=True)) == ["gen_4b", "gen_14b"]
    assert required_generation_endpoints(ProbeContext(is_production=False)) == ["gen_4b"]


def test_required_generation_matches_resolve_model_endpoint_contract() -> None:
    """The required set must equal what the routing config actually selects.

    This is what stops a future config change from silently making the readiness
    contract disagree with the code that serves requests.
    """
    from common.config import get_config_dict, resolve_model_endpoint

    tiers = get_config_dict()["model_routing"]["tiers"]
    ctx = ProbeContext(is_production=False)
    selected = {resolve_model_endpoint(tier, is_production=False) for tier in tiers}
    # Non-production: `complex` is rewritten to `simple`, so gen_4b is the only
    # endpoint the deployment can route to.
    assert selected == set(required_generation_endpoints(ctx))


# ---------------------------------------------------------------------------
# 11. Retrieval down AND generation down.
# ---------------------------------------------------------------------------


def test_retrieval_and_generation_down_is_not_ready() -> None:
    report = _evaluate(("qdrant", "elasticsearch", "gen_4b", "gen_14b"))
    assert report.ready is False
    assert "retrieval" in report.blockers
    assert "gen_4b" in report.blockers
    assert "gen_14b" in report.blockers
    # Degradable dependencies are still reported, just not as blockers.
    assert report.degraded == []


# ---------------------------------------------------------------------------
# 12. Probe failures are normalized, never propagated.
# ---------------------------------------------------------------------------


def test_raising_probe_becomes_unhealthy_not_an_exception() -> None:
    def boom(_ctx):
        raise RuntimeError("probe exploded")

    probes = _probes(ALL_UP)
    probes["qdrant"] = boom
    report = evaluate_readiness(ProbeContext(is_production=True), probes)
    # Elasticsearch still satisfies the retrieval OR, so this stays servable.
    assert report.ready is True
    assert report.dependencies["qdrant"] is False
    assert "qdrant" in report.degraded


def test_raising_probe_on_a_required_path_blocks_readiness() -> None:
    def boom(_ctx):
        raise RuntimeError("probe exploded")

    probes = _probes(ALL_UP)
    probes["gen_4b"] = boom
    report = evaluate_readiness(ProbeContext(is_production=True), probes)
    assert report.ready is False
    assert "gen_4b" in report.blockers


def test_missing_probe_is_not_evidence_of_health() -> None:
    """A probe that was never supplied must not be assumed reachable."""
    probes = _probes(ALL_UP)
    del probes["elasticsearch"]
    report = evaluate_readiness(ProbeContext(is_production=True), probes)
    assert report.dependencies["elasticsearch"] is False
    # Qdrant still satisfies the retrieval OR.
    assert report.ready is True


# ---------------------------------------------------------------------------
# 13. Payload hygiene: no credentials, no addresses, no internal detail.
# ---------------------------------------------------------------------------


def test_payload_contains_only_dependency_names_and_booleans() -> None:
    payload = _evaluate(("redis", "qdrant")).to_payload()
    assert set(payload) == {"status", "dependencies", "degraded", "blockers"}
    assert set(payload["dependencies"]) == set(DEPENDENCY_KEYS)
    assert all(isinstance(v, bool) for v in payload["dependencies"].values())
    assert all(isinstance(v, str) for v in payload["degraded"] + payload["blockers"])


def test_payload_never_leaks_configured_urls_or_secrets() -> None:
    """A probe configured against a credentialed host must not echo it."""
    ctx = ProbeContext(
        is_production=True,
        endpoints={"gen_4b": "http://vllm.internal:8101", "gen_14b": "http://vllm.internal:8100"},
        elasticsearch={"host": "http://es.internal:9200", "username": "elastic", "password": "s3cr3t"},
        qdrant={"host": "qdrant.internal", "port": 6333},
        redis={"host": "redis.internal", "password": "hunter2"},
        minio={"endpoint": "minio.internal:9000", "secret_key": "minio-secret"},
    )

    def fake_generation(_ctx):
        return True

    probes = _probes(ALL_UP)
    probes["gen_4b"] = fake_generation
    probes["gen_14b"] = fake_generation
    report = evaluate_readiness(ctx, probes)
    payload = report.to_payload()
    serialized = repr(payload)

    # `elastic` is deliberately NOT asserted here: it is a substring of the
    # dependency key "elasticsearch", which the contract must expose. The
    # username leak it stands for is covered by the value-shape assertion below.
    for secret in (
        "s3cr3t",
        "hunter2",
        "minio-secret",
        "es.internal",
        "qdrant.internal",
        "redis.internal",
        "minio.internal",
        "vllm.internal",
        "http://",
    ):
        assert secret not in serialized, f"readiness payload leaked {secret!r}"

    # Stronger than substring checks: every string in the payload must be a
    # declared dependency name or a status literal. Nothing else can appear.
    allowed = set(DEPENDENCY_KEYS) | {"retrieval", "readiness_evaluator", "ready", "not_ready"}
    strings = set(payload["dependencies"]) | set(payload["degraded"]) | set(payload["blockers"])
    assert {payload["status"], *strings} <= allowed


def test_report_status_property_matches_ready_flag() -> None:
    assert _evaluate().status == "ready"
    assert _evaluate(("qdrant", "elasticsearch")).status == "not_ready"


# ---------------------------------------------------------------------------
# 14. The generation probe uses /v1/models, not a real generation request.
# ---------------------------------------------------------------------------


def test_generation_probe_uses_v1_models() -> None:
    """No tokens may be generated as part of a readiness probe."""
    seen: dict[str, str] = {}

    class _Response:
        status_code = 200

    class _FakeClient:
        def __init__(self, timeout=None):
            seen["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url):
            seen["url"] = url
            return _Response()

    import httpx

    original = httpx.Client
    httpx.Client = _FakeClient
    try:
        probe = probe_generation("gen_4b")
        ok = probe(ProbeContext(is_production=True, endpoints={"gen_4b": "http://vllm:8101"}))
    finally:
        httpx.Client = original

    assert ok is True
    assert seen["url"] == "http://vllm:8101/v1/models"
    assert "chat/completions" not in seen["url"]
    assert seen["timeout"] is not None, "the probe must bound its own timeout"


def test_generation_probe_rejects_non_200() -> None:
    class _Response:
        status_code = 503

    class _FakeClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url):
            return _Response()

    import httpx

    original = httpx.Client
    httpx.Client = _FakeClient
    try:
        probe = probe_generation("gen_4b")
        assert probe(ProbeContext(is_production=True, endpoints={"gen_4b": "http://vllm:8101"})) is False
    finally:
        httpx.Client = original


def test_generation_probe_without_a_url_is_unavailable() -> None:
    probe = probe_generation("gen_4b")
    assert probe(ProbeContext(is_production=True, endpoints={})) is False


# ---------------------------------------------------------------------------
# 15. Payload order is stable (byte-stable responses).
# ---------------------------------------------------------------------------


def test_degraded_and_blockers_are_stable_and_deduplicated() -> None:
    first = _evaluate(("redis", "minio", "qdrant")).to_payload()
    second = _evaluate(("redis", "minio", "qdrant")).to_payload()
    assert first == second
    # DEPENDENCY_KEYS order: redis, qdrant, elasticsearch, minio, gen_4b, gen_14b
    assert first["degraded"] == ["redis", "qdrant", "minio"]
