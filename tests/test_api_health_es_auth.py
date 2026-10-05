"""Regression tests for the Elasticsearch credential contract of ``GET /api/health``.

The canonical Compose deployment enables ``xpack.security.enabled=true``
(``docker-compose.yml``, Elasticsearch service) and injects
``ELASTICSEARCH_USERNAME`` / ``ELASTICSEARCH_PASSWORD`` into the app container.
``retrieval/bm25_retriever.py`` resolves those credentials before it can talk to
the cluster, but the health sub-check used to build ``Elasticsearch([host])``
with no authentication at all.

On such a deployment an anonymous ``ping()`` is rejected, so a perfectly
reachable, correctly credentialed Elasticsearch was reported as
``dependencies.elasticsearch=false`` — the endpoint reported "degraded" for the
wrong reason, and any readiness contract layered on top of it would inherit a
false ES signal.

These tests pin the contract, not a deployment:

* credentials resolve environment-first with a ``config.json`` fallback, exactly
  like ``BM25Retriever.es_client``;
* ``basic_auth`` is only set when username **and** password are both non-empty;
* a client exception or a falsy ``ping()`` still degrades to
  ``dependencies.elasticsearch=false`` instead of crashing the endpoint;
* the health response shape is unchanged, so nothing downstream has to move.

Every test is deterministic: the ``elasticsearch`` module is replaced with a
recording fake, so no cluster, socket or credential is involved, and the
Redis/Qdrant sub-checks are pinned so only the ES signal is under test. Nothing
here claims a real Elasticsearch run.
"""

from __future__ import annotations

import copy
import sys
import types

import pytest

#: Host used by every test so a stray real client can never look "reachable".
FAKE_ES_HOST = "http://es.invalid:9200"

#: An ``elasticsearch`` section shaped like ``config.json`` (empty credentials).
ES_CONFIG = {
    "host": FAKE_ES_HOST,
    "index": "cosmetics_docs",
    "enabled": True,
    "username": "",
    "password": "",
}


class _FakeElasticsearch:
    """Records constructor kwargs and answers ``ping()`` from a scripted outcome."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.kwargs = {}
        self.ping_calls = 0

    def ping(self, *args, **kwargs):
        self.ping_calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def info(self):
        """``BM25Retriever.es_client`` reads the version right after connecting."""
        return {"version": {"number": "8.12.0"}}


@pytest.fixture(autouse=True)
def healthy_sibling_dependencies(monkeypatch):
    """Pin the Redis/Qdrant sub-checks to healthy so only the ES signal is read.

    These two branches are out of scope here; leaving them to whatever happens to
    run locally would make the endpoint's ``status`` depend on the developer
    machine instead of on the Elasticsearch credential under test.
    """
    import cache.redis_cache as redis_cache_module
    from api import routes

    class _FakeRedisCache:
        enabled = True

    class _FakeQdrantClient:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def healthcheck(self):
            return True

    monkeypatch.setattr(redis_cache_module, "RedisCache", _FakeRedisCache)
    monkeypatch.setattr(routes, "QdrantClient", _FakeQdrantClient)


@pytest.fixture
def es_spy(monkeypatch):
    """Install a fake ``elasticsearch`` module and expose the clients it built.

    Returns a namespace with:
        ``created``  — every client the code under test constructed, in order;
        ``kwargs()`` — constructor kwargs of the single expected client;
        ``state``    — mutable ``outcome`` driving ``ping()`` (an exception is raised).
    """
    created: list[_FakeElasticsearch] = []
    state: dict[str, object] = {"outcome": True}

    def factory(*args, **kwargs):
        client = _FakeElasticsearch(state["outcome"])
        # Normalise the host so assertions test the credential contract rather
        # than whether the caller passed the host positionally or by keyword.
        client.kwargs = {"hosts": list(args[0]) if args else kwargs.get("hosts"), **kwargs}
        created.append(client)
        return client

    monkeypatch.setitem(sys.modules, "elasticsearch", types.SimpleNamespace(Elasticsearch=factory))

    def kwargs():
        assert len(created) == 1, f"expected exactly one Elasticsearch client, got {len(created)}"
        return created[0].kwargs

    return types.SimpleNamespace(created=created, kwargs=kwargs, state=state)


@pytest.fixture
def health_config(monkeypatch):
    """Point ``api.routes._config`` at an isolated, in-memory Elasticsearch section."""
    from api import routes

    cfg = copy.deepcopy(routes._config)
    cfg.setdefault("system", {})["version"] = "test-version"
    cfg["elasticsearch"] = copy.deepcopy(ES_CONFIG)
    monkeypatch.setattr(routes, "_config", cfg)
    return cfg


@pytest.fixture
def no_es_env(monkeypatch):
    """Remove both credential variables so config / anonymous behaviour is testable."""
    monkeypatch.delenv("ELASTICSEARCH_USERNAME", raising=False)
    monkeypatch.delenv("ELASTICSEARCH_PASSWORD", raising=False)


def _health_body():
    """Run the real sub-check assembly without the HTTP layer."""
    from api.routes import health_handler

    return health_handler()


# ── credentials are forwarded ───────────────────────────────────────────────
def test_env_credentials_reach_basic_auth(es_spy, health_config, monkeypatch):
    """The Compose-injected env pair must authenticate the health client.

    Regression: the sub-check built ``Elasticsearch([host])`` anonymously, so an
    authenticated cluster answered 401 and the endpoint reported degraded.
    """
    monkeypatch.setenv("ELASTICSEARCH_USERNAME", "elastic")
    monkeypatch.setenv("ELASTICSEARCH_PASSWORD", "compose-password")

    body = _health_body()

    kwargs = es_spy.kwargs()
    assert kwargs["hosts"] == [FAKE_ES_HOST], "health must probe the configured Elasticsearch host"
    assert kwargs["basic_auth"] == ("elastic", "compose-password"), (
        "the health client dropped the env credentials the runtime client uses; "
        "an authenticated Elasticsearch will be reported as degraded"
    )
    assert es_spy.created[0].ping_calls == 1
    assert body.dependencies["elasticsearch"] is True
    assert body.status == "healthy", (
        "a credentialed Elasticsearch that pings must make the whole endpoint healthy, "
        "not merely flip one boolean while the status stays degraded"
    )


def test_config_credentials_are_used_when_env_is_absent(es_spy, health_config, no_es_env):
    """config.json remains the documented fallback, not a dead branch."""
    health_config["elasticsearch"].update({"username": "cfg-user", "password": "cfg-password"})

    _health_body()

    assert es_spy.kwargs()["basic_auth"] == ("cfg-user", "cfg-password")


# ── environment really wins over config ─────────────────────────────────────
def test_env_overrides_config_credentials(es_spy, health_config, monkeypatch):
    """Env precedence is the operator's lever; config must not silently win."""
    health_config["elasticsearch"].update({"username": "cfg-user", "password": "cfg-password"})
    monkeypatch.setenv("ELASTICSEARCH_USERNAME", "env-user")
    monkeypatch.setenv("ELASTICSEARCH_PASSWORD", "env-password")

    _health_body()

    assert es_spy.kwargs()["basic_auth"] == ("env-user", "env-password"), (
        "ELASTICSEARCH_USERNAME/ELASTICSEARCH_PASSWORD must override config.json, matching "
        "BM25Retriever; otherwise health authenticates as a different principal than retrieval"
    )


# ── the both-present rule ───────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("username", "password", "reason"),
    [
        ("elastic", "", "the password is missing"),
        ("", "some-password", "the username is missing"),
        ("", "", "neither is set"),
    ],
)
def test_basic_auth_requires_both_username_and_password(es_spy, health_config, monkeypatch, username, password, reason):
    """Half a credential must stay anonymous, never become ``("", "pw")`` auth."""
    monkeypatch.setenv("ELASTICSEARCH_USERNAME", username)
    monkeypatch.setenv("ELASTICSEARCH_PASSWORD", password)

    _health_body()

    assert "basic_auth" not in es_spy.kwargs(), (
        f"basic_auth must stay unset when {reason}; an incomplete pair sends a malformed "
        "Authorization header instead of the anonymous call the runtime client makes"
    )


def test_blank_env_falls_through_to_config(es_spy, health_config, monkeypatch):
    """An empty env value must not shadow a usable config credential."""
    health_config["elasticsearch"].update({"username": "cfg-user", "password": "cfg-password"})
    monkeypatch.setenv("ELASTICSEARCH_USERNAME", "")
    monkeypatch.setenv("ELASTICSEARCH_PASSWORD", "")

    _health_body()

    assert es_spy.kwargs()["basic_auth"] == ("cfg-user", "cfg-password")


# ── unauthenticated deployments stay compatible ─────────────────────────────
def test_no_credential_config_stays_anonymous_and_reports_healthy(es_spy, health_config, no_es_env):
    """A deployment without xpack security must keep working exactly as before."""
    body = _health_body()

    assert es_spy.kwargs() == {"hosts": [FAKE_ES_HOST]}, (
        "the anonymous call shape changed; deployments without ES security must keep working "
        "and the client must not gain unrelated options"
    )
    assert es_spy.created[0].ping_calls == 1
    assert body.dependencies["elasticsearch"] is True


def test_missing_elasticsearch_section_uses_the_runtime_default(es_spy, health_config, no_es_env, monkeypatch):
    """No ES section must not raise; the fallback host is the runtime client's default."""
    monkeypatch.setitem(health_config, "elasticsearch", {})

    body = _health_body()

    assert es_spy.kwargs()["hosts"] == ["http://localhost:9200"], (
        "the fallback host must match BM25Retriever's default, otherwise health probes a "
        "different cluster than the one retrieval reads"
    )
    assert body.dependencies["elasticsearch"] is True


# ── failure stays a degraded dependency, never a crash ──────────────────────
@pytest.mark.parametrize(
    "outcome",
    [
        False,
        RuntimeError("AuthenticationException: 401 unauthorized"),
    ],
    ids=["ping-false", "client-raises"],
)
def test_failure_reports_elasticsearch_false_without_crashing(es_spy, health_config, no_es_env, outcome):
    """Bad credentials / unreachable ES stay a degraded sub-check on an HTTP 200.

    The endpoint is a diagnostic: it reports the failure instead of raising, so
    the pre-existing degraded semantics survive the credential change.
    """
    es_spy.state["outcome"] = outcome

    body = _health_body()

    assert body.dependencies["elasticsearch"] is False, (
        "a failing Elasticsearch sub-check must report false so /api/health degrades"
    )
    assert body.status == "degraded"
    assert set(body.dependencies) == {"redis", "qdrant", "elasticsearch"}, "the dependency set must not change"


# ── the endpoint contract itself is unchanged ────────────────────────────────
def test_health_endpoint_returns_200_with_the_same_schema(es_spy, health_config, monkeypatch):
    """Over HTTP: an authenticated ES that pings yields the unchanged healthy body."""
    from fastapi.testclient import TestClient

    import app as application

    monkeypatch.setenv("ELASTICSEARCH_USERNAME", "elastic")
    monkeypatch.setenv("ELASTICSEARCH_PASSWORD", "compose-password")
    client = TestClient(application.app)

    response = client.get("/api/health")

    assert response.status_code == 200, "/api/health must stay a 200 diagnostic endpoint"
    body = response.json()
    assert set(body) == {"status", "version", "dependencies"}, (
        f"health response shape changed to {sorted(body)}; downstream readers (the tcpSocket "
        "liveness rationale, future readiness work) depend on it"
    )
    assert body["version"] == "test-version"
    assert body["dependencies"]["elasticsearch"] is True, (
        "an authenticated Elasticsearch that pings successfully must not be reported degraded"
    )
    assert body["status"] == "healthy"


def test_health_endpoint_survives_an_auth_failure(es_spy, health_config, no_es_env):
    """HTTP 200 with ``elasticsearch=false`` when the cluster rejects the call."""
    from fastapi.testclient import TestClient

    import app as application

    es_spy.state["outcome"] = RuntimeError("AuthenticationException: 401 unauthorized")
    client = TestClient(application.app)

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "version", "dependencies"}
    assert body["dependencies"]["elasticsearch"] is False
    assert body["status"] == "degraded"


# ── drift guard: the health check and the runtime client must not diverge ────
def test_health_kwargs_match_the_bm25_runtime_client(es_spy, health_config, monkeypatch):
    """Same host and same credentials as ``BM25Retriever.es_client``.

    This is what makes holding the contract in two places safe: if either side
    changes precedence, the both-present rule or the default host, this test
    fails instead of the health endpoint quietly probing anonymously.
    """
    import retrieval.bm25_retriever as bm25_module
    from api.routes import _elasticsearch_client_kwargs
    from retrieval.bm25_retriever import BM25Retriever

    monkeypatch.setenv("ELASTICSEARCH_USERNAME", "elastic")
    monkeypatch.setenv("ELASTICSEARCH_PASSWORD", "compose-password")

    expected = _elasticsearch_client_kwargs(copy.deepcopy(ES_CONFIG))

    _health_body()
    assert es_spy.kwargs() == expected
    assert expected["basic_auth"] == ("elastic", "compose-password"), (
        "the shared resolver must apply the env credentials to both callers"
    )

    bm25_config = copy.deepcopy(bm25_module.config)
    bm25_config["elasticsearch"] = copy.deepcopy(ES_CONFIG)
    monkeypatch.setattr(bm25_module, "config", bm25_config)
    es_spy.created.clear()

    retriever = BM25Retriever()
    client = retriever.es_client

    assert client is not None and retriever.enabled is True, "BM25 must stay enabled for a credentialed client"
    assert es_spy.kwargs() == expected, (
        "the health check and BM25Retriever must resolve identical Elasticsearch client kwargs; "
        "they read the same cluster, so they must share one credential contract"
    )
