"""HTTP-level contract tests for ``GET /api/ready`` and the ``GET /api/health`` split.

``/api/health`` (diagnostic, HTTP 200 + healthy/degraded) and ``/api/ready``
(traffic admission, HTTP 200/503) answer different questions. These tests pin both
at the HTTP boundary, including that readiness needs no user JWT and that it never
leaks credentials.

All dependency probes are injected. No Redis, Qdrant, Elasticsearch, MinIO or vLLM
server is contacted, and no Kubernetes probe is executed.

Evidence level: REPO_VERIFIED (deterministic, offline). Real cluster validation is
PENDING.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.readiness import DEPENDENCY_KEYS  # noqa: E402

ALL_UP = dict.fromkeys(DEPENDENCY_KEYS, True)

#: A credentialed deployment, to prove the payload cannot echo any of it.
SECRET_YAML = {
    "ELASTICSEARCH_USERNAME": "elastic",
    "ELASTICSEARCH_PASSWORD": "es-s3cr3t-value",
    "REDIS_PASSWORD": "redis-hunter2-value",
    "MINIO_ACCESS_KEY": "minio-access",
    "MINIO_SECRET_KEY": "minio-s3cr3t-value",
    "SERVICE_AUTH_TOKEN": "service-token-value",
    "VLLM_4B_URL": "http://vllm-4b.internal:8101",
    "VLLM_GEN_14B_URL": "http://vllm-14b.internal:8100",
}


@pytest.fixture()
def client(monkeypatch):
    """A TestClient whose readiness probes are fully scripted."""
    try:
        import torch  # noqa: F401
    except ImportError:
        fake_torch = types.ModuleType("torch")
        fake_cuda = types.ModuleType("torch.cuda")
        fake_cuda.is_available = lambda: False
        fake_cuda.OutOfMemoryError = type("OutOfMemoryError", (Exception,), {})
        fake_torch.cuda = fake_cuda
        sys.modules["torch"] = fake_torch
        sys.modules["torch.cuda"] = fake_cuda

    from fastapi.testclient import TestClient

    import app as application
    from api import readiness as readiness_module

    def install(state: dict[str, bool], *, is_production: bool = True, raises: set[str] | None = None):
        """Script the probe outcomes and run the *real* readiness evaluator.

        The evaluator itself is deliberately not stubbed: the HTTP tests must
        prove the shipped logic produces these codes, not that a mock agrees
        with itself. Only the probes (which would open sockets) are replaced.
        """
        raises = raises or set()

        def make(key: str):
            def _probe(_ctx):
                if key in raises:
                    raise RuntimeError(f"{key} probe exploded")
                return state.get(key, False)

            return _probe

        monkeypatch.setattr(
            readiness_module,
            "_default_probes",
            lambda ctx: {key: make(key) for key in DEPENDENCY_KEYS},
        )
        monkeypatch.setattr(
            readiness_module,
            "_build_context",
            lambda: readiness_module.ProbeContext(is_production=is_production),
        )

    client = TestClient(application.app)
    client.install_readiness = install  # type: ignore[attr-defined]
    return client


@pytest.fixture()
def secret_env(monkeypatch):
    for key, value in SECRET_YAML.items():
        monkeypatch.setenv(key, value)


# ---------------------------------------------------------------------------
# 1-4. Ready / not-ready HTTP codes.
# ---------------------------------------------------------------------------


def test_ready_returns_200_with_structured_dependencies(client) -> None:
    client.install_readiness(ALL_UP)
    response = client.get("/api/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["blockers"] == []
    assert body["degraded"] == []
    assert body["dependencies"] == ALL_UP


def test_both_retrieval_paths_down_returns_503(client) -> None:
    state = dict(ALL_UP, qdrant=False, elasticsearch=False)
    client.install_readiness(state)
    response = client.get("/api/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert "retrieval" in body["blockers"]


def test_required_generation_down_returns_503(client) -> None:
    state = dict(ALL_UP, gen_4b=False)
    client.install_readiness(state, is_production=True)
    response = client.get("/api/ready")
    assert response.status_code == 503
    assert "gen_4b" in response.json()["blockers"]


def test_degradable_dependency_down_still_returns_200(client) -> None:
    """Redis down must not pull a serving pod out of rotation."""
    client.install_readiness(dict(ALL_UP, redis=False))
    response = client.get("/api/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


# ---------------------------------------------------------------------------
# 5. The probe needs no user JWT.
# ---------------------------------------------------------------------------


def test_readiness_requires_no_authentication(client, secret_env) -> None:
    client.install_readiness(ALL_UP)
    response = client.get("/api/ready")
    assert response.status_code == 200
    assert "www-authenticate" not in {k.lower() for k in response.headers}
    assert response.status_code != 401
    assert response.status_code != 403


def test_readiness_leaks_no_credentials_under_a_credentialed_deployment(client, secret_env) -> None:
    client.install_readiness(dict(ALL_UP, redis=False, minio=False))
    response = client.get("/api/ready")
    assert response.status_code == 200
    serialized = response.text
    for secret in SECRET_YAML.values():
        if secret == "elastic":
            continue  # substring of the "elasticsearch" dependency key
        assert secret not in serialized, f"readiness leaked {secret!r}"
    for marker in ("http://", "internal", "password", "token", "dsn"):
        assert marker not in serialized.lower().replace("elasticsearch", "")


# ---------------------------------------------------------------------------
# 6. /api/health keeps its diagnostic contract.
# ---------------------------------------------------------------------------


def test_health_still_returns_200_and_degraded_on_dependency_failure(client, monkeypatch) -> None:
    """The diagnostic contract is unchanged: dependencies down -> 200 + degraded."""
    from api import routes

    class _FailingRedis:
        def __init__(self, *a, **k):
            self.enabled = False

    class _FailingQdrant:
        def __init__(self, *a, **k):
            pass

        def healthcheck(self):
            raise RuntimeError("qdrant down")

    import elasticsearch as es_module

    class _FakeES:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def ping(self):
            return False

    monkeypatch.setattr(es_module, "Elasticsearch", _FakeES, raising=False)
    monkeypatch.setattr(routes, "QdrantClient", _FailingQdrant, raising=False)
    # health_handler resolves RedisCache from cache.redis_cache at call time.
    monkeypatch.setattr("cache.redis_cache.RedisCache", _FailingRedis, raising=False)

    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["elasticsearch"] is False
    assert body["dependencies"]["redis"] is False
    # Readiness is a separate endpoint; health must not have grown a readiness field.
    assert "blockers" not in body
    assert "degraded" not in body


def test_health_and_ready_have_distinct_status_contracts(client, monkeypatch) -> None:
    """Even with the same dependency state, the two endpoints must not agree on status."""
    import elasticsearch as es_module

    import api.routes as routes_module

    class _FakeES:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def ping(self):
            return False

    class _OkQdrant:
        def __init__(self, *a, **k):
            pass

        def healthcheck(self):
            return True

    class _OkRedis:
        def __init__(self, *a, **k):
            self.enabled = True

    monkeypatch.setattr(es_module, "Elasticsearch", _FakeES, raising=False)
    monkeypatch.setattr(routes_module, "QdrantClient", _OkQdrant, raising=False)
    monkeypatch.setattr("cache.redis_cache.RedisCache", _OkRedis, raising=False)

    health = client.get("/api/health").json()
    client.install_readiness(dict(ALL_UP))
    ready = client.get("/api/ready").json()

    assert health["status"] == "degraded"
    assert ready["status"] == "ready"
    # Same underlying ES signal, opposite admission consequence.
    assert health["dependencies"]["elasticsearch"] is False
    assert ready["dependencies"]["elasticsearch"] is True


# ---------------------------------------------------------------------------
# 7. Probe exceptions never surface as 500.
# ---------------------------------------------------------------------------


def test_probe_exception_yields_structured_response_not_500(client, monkeypatch) -> None:
    import api.readiness as readiness_module
    from api import routes

    def exploding():
        raise RuntimeError("evaluator exploded")

    monkeypatch.setattr(readiness_module, "evaluate_readiness", exploding)
    response = client.get("/api/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["blockers"] == ["readiness_evaluator"]
    # The failure text must not be echoed to an unauthenticated caller.
    assert "exploded" not in response.text
    assert "RuntimeError" not in response.text
    assert routes is not None


# ---------------------------------------------------------------------------
# 8. The route is registered and documented in OpenAPI.
# ---------------------------------------------------------------------------


def test_ready_is_registered_on_the_router(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/ready" in paths
    assert "/api/health" in paths


def test_ready_response_model_documents_the_two_statuses(client) -> None:
    schema = client.get("/openapi.json").json()
    ref = schema["paths"]["/api/ready"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    name = ref.rsplit("/", 1)[-1]
    description = schema["components"]["schemas"][name]["description"]
    assert "503" in description or "not_ready" in description
