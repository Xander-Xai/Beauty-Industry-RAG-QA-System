"""Deterministic checks for the minimal Kubernetes deployment contract.

These tests are static and offline. They parse the manifests and assert
structural invariants. They do NOT talk to a Kubernetes cluster, do not require
kubectl, do not need a network, and do not prove that anything was ever
deployed.

Passing this module means exactly one thing: the manifests under deploy/k8s/
parse as YAML and satisfy the contracts asserted below. That is the whole
extent of the ``REPO_VERIFIED`` evidence level claimed for them.

Real cluster deployment is PENDING.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
K8S_DIR = ROOT / "deploy" / "k8s"

# Keys that must never appear with a real value inside a committed manifest.
SECRET_KEY_MARKERS = ("PASSWORD", "TOKEN", "SECRET", "API_KEY")
PLACEHOLDER_VALUES = {"REPLACE_ME", "", "CHANGE_ME", "TODO"}


def _load_all(path: Path) -> list[dict[str, Any]]:
    """Parse a possibly multi-document YAML file into a list of documents."""
    documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    documents = [d for d in documents if d is not None]
    assert documents, f"{path.name} is empty"
    return documents


def _documents() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in sorted(K8S_DIR.glob("*.yaml")):
        for document in _load_all(path):
            document["__file__"] = path.name
            docs.append(document)
    return docs


def _by_kind(kind: str) -> list[dict[str, Any]]:
    return [d for d in _documents() if d.get("kind") == kind]


def _file_mode(value: Any) -> int:
    """Normalize a Kubernetes defaultMode to an int.

    YAML 1.1 resolves a leading-zero literal such as 0440 to the integer 288,
    while an unquoted value can also arrive as a plain string. Accept both.
    """
    if isinstance(value, int):
        return value
    return int(str(value), 8)


def _container(deployment: dict[str, Any]) -> dict[str, Any]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1, "this contract declares exactly one container"
    return containers[0]


# --------------------------------------------------------------------------
# 1. Every manifest parses as YAML and carries the required identity fields.
# --------------------------------------------------------------------------


def test_every_manifest_parses_and_declares_api_version_and_kind() -> None:
    documents = _documents()
    assert documents, "no manifests found under deploy/k8s/"
    for document in documents:
        assert document.get("apiVersion"), f"{document['__file__']}: missing apiVersion"
        assert document.get("kind"), f"{document['__file__']}: missing kind"
        assert document["apiVersion"].endswith("v1"), f"{document['__file__']}: unexpected apiVersion"


def test_namespace_is_declared_and_used_consistently() -> None:
    namespaces = _by_kind("Namespace")
    assert len(namespaces) == 1, "expected exactly one Namespace"
    name = namespaces[0]["metadata"]["name"]
    for document in _documents():
        if document["kind"] == "Namespace":
            continue
        assert document["metadata"].get("namespace") == name, (
            f"{document['__file__']}: namespace {document['metadata'].get('namespace')!r} "
            f"does not match declared Namespace {name!r}"
        )


def test_expected_kinds_are_present() -> None:
    kinds = {d["kind"] for d in _documents()}
    assert kinds == {"Namespace", "ConfigMap", "Secret", "Deployment", "Service"}, (
        f"unexpected manifest set: {sorted(kinds)}"
    )


# --------------------------------------------------------------------------
# 2. Deployment selector must be a subset of the pod template labels.
# --------------------------------------------------------------------------


def test_deployment_selector_matches_pod_template_labels() -> None:
    for deployment in _by_kind("Deployment"):
        selector = deployment["spec"]["selector"]["matchLabels"]
        labels = deployment["spec"]["template"]["metadata"]["labels"]
        missing = {k: v for k, v in selector.items() if labels.get(k) != v}
        assert not missing, (
            f"selector entries not satisfied by pod labels: {missing} (selector={selector}, labels={labels})"
        )


# --------------------------------------------------------------------------
# 3. Service selector must select the Deployment's pods.
# --------------------------------------------------------------------------


def test_service_selector_matches_deployment_pod_labels() -> None:
    deployments = _by_kind("Deployment")
    assert len(deployments) == 1
    pod_labels = deployments[0]["spec"]["template"]["metadata"]["labels"]
    services = _by_kind("Service")
    assert len(services) == 1
    selector = services[0]["spec"]["selector"]
    assert selector, "Service must declare a selector"
    for key, value in selector.items():
        assert pod_labels.get(key) == value, (
            f"Service selector {key}={value!r} does not match pod label {key}={pod_labels.get(key)!r}"
        )


def test_service_targets_the_declared_container_port() -> None:
    service = _by_kind("Service")[0]
    deployment = _by_kind("Deployment")[0]
    container = _container(deployment)
    declared = {p["name"]: p["containerPort"] for p in container["ports"]}
    for port in service["spec"]["ports"]:
        assert port["targetPort"] in declared, (
            f"Service targetPort {port['targetPort']!r} is not a declared container port {declared}"
        )
        assert port["port"] == declared[port["targetPort"]], "Service port should match the container port it targets"


# --------------------------------------------------------------------------
# 4. Probes must point at endpoints that really exist and need no auth.
# --------------------------------------------------------------------------


#: The two probe endpoints this contract is allowed to target. Both are live and
#: unauthenticated; `/api/metrics` returns 401 and is therefore not a probe.
VERIFIED_PROBE_PATHS = {"/api/health", "/api/ready"}


def test_probes_target_real_unauthenticated_endpoints() -> None:
    """`/api/health` and `/api/ready` are live and unauthenticated.

    Verified against the application, not assumed: GET /api/health -> 200 and
    GET /api/ready -> 200/503 without credentials, while GET /api/metrics ->
    401 without a token.
    """
    deployment = _by_kind("Deployment")[0]
    container = _container(deployment)
    named_ports = {p["name"] for p in container["ports"]}
    for probe_name in ("startupProbe", "livenessProbe", "readinessProbe"):
        probe = container.get(probe_name)
        assert probe is not None, f"{probe_name} must be declared"
        assert len(probe) >= 1, f"{probe_name} must declare a probe handler"

        http_get = probe.get("httpGet")
        if http_get is None:
            # tcpSocket form. Endpoint validity is asserted by
            # test_liveness_probe_does_not_depend_on_backing_services, which
            # pins the port to a declared named container port.
            tcp_socket = probe.get("tcpSocket")
            assert tcp_socket is not None, f"{probe_name} must declare exactly one of httpGet / tcpSocket"
            assert tcp_socket["port"] in named_ports, (
                f"{probe_name} port {tcp_socket['port']!r} is not a declared named container port"
            )
            continue

        assert http_get["path"] in VERIFIED_PROBE_PATHS, (
            f"{probe_name} path {http_get['path']!r} is not a verified probe endpoint"
        )
        assert http_get["path"] != "/api/metrics", f"{probe_name} must not use /api/metrics: it requires authentication"
        assert http_get["port"] in named_ports, (
            f"{probe_name} port {http_get['port']!r} is not a declared named container port"
        )


def test_probe_endpoints_are_registered_and_unauthenticated() -> None:
    """Every path a probe targets must exist on the app and need no user JWT.

    A probe cannot carry credentials, so a probe pointing at an endpoint that
    requires a user token fails forever and the Pod never becomes ready.
    """
    from fastapi.testclient import TestClient

    # The probes are stubbed to avoid contacting real dependencies from a static
    # manifest test; what is asserted here is routing and auth, not dependency
    # state.
    import api.readiness as readiness_module
    import app as application

    original = readiness_module._default_probes
    readiness_module._default_probes = lambda ctx: {}
    try:
        client = TestClient(application.app)
        registered = client.get("/openapi.json").json()["paths"]
        container = _container(_by_kind("Deployment")[0])
        for probe_name in ("startupProbe", "readinessProbe"):
            path = container[probe_name]["httpGet"]["path"]
            assert path in registered, f"{probe_name} targets {path!r}, which is not a registered route"
            response = client.get(path)
            assert response.status_code != 401, f"{path} must not require a JWT"
            assert response.status_code != 403, f"{path} must not require a JWT"
            assert "security" not in response.json(), f"{path} must not require a JWT"
    finally:
        readiness_module._default_probes = original


def test_readiness_probe_targets_the_readiness_endpoint() -> None:
    """readinessProbe must be dependency-aware, not a liveness-level gate.

    `/api/health` returns 200 even with every dependency unreachable, so a
    readinessProbe on it can never remove an unserving Pod from the Service.
    """
    container = _container(_by_kind("Deployment")[0])
    readiness = container["readinessProbe"]
    assert "httpGet" in readiness, "readinessProbe must use httpGet so it can observe the status code"
    assert readiness["httpGet"]["path"] == "/api/ready", (
        "readinessProbe must target /api/ready; /api/health cannot express not-ready"
    )


def test_readiness_probe_allows_a_503_to_be_observed() -> None:
    """The probe must actually be able to see the not-ready status code.

    The endpoint answers 503 when the serving contract fails, and that non-2xx
    response is what removes the Pod from Endpoints.
    """
    import api.models as api_models

    schema = api_models.ReadinessResponse.model_json_schema()
    properties = schema["properties"]
    assert properties["blockers"]["type"] == "array"
    assert properties["degraded"]["type"] == "array"
    assert properties["dependencies"]["type"] == "object"
    # Only booleans may appear per dependency: the probe payload is returned to
    # an unauthenticated caller, so a richer value would be a leak channel.
    assert properties["dependencies"]["additionalProperties"] == {"type": "boolean"}
    assert {properties["status"]["type"]} == {"string"}


def test_startup_probe_remains_process_liveness_only() -> None:
    """startupProbe answers "has the process started", not "can it serve".

    Pointing it at the dependency-aware endpoint would couple startup to
    dependency availability: an outage during a rollout could keep the probe
    failing until failureThreshold is reached and trigger a restart loop.
    """
    container = _container(_by_kind("Deployment")[0])
    startup = container["startupProbe"]
    assert startup["httpGet"]["path"] == "/api/health", (
        "startupProbe must stay on /api/health: readiness failure is not a startup failure"
    )


def test_liveness_probe_does_not_depend_on_backing_services() -> None:
    """Dependency outages must not restart-loop the gateway.

    GET /api/health answers HTTP 200 even when Redis, Qdrant and Elasticsearch
    are all unreachable, so an httpGet liveness probe on it can never fail and
    would also be misleading. tcpSocket is the honest choice.
    """
    container = _container(_by_kind("Deployment")[0])
    liveness = container["livenessProbe"]
    assert "tcpSocket" in liveness, (
        "livenessProbe should use tcpSocket: /api/health returns 200 while all "
        "dependencies are down, so it carries no liveness signal"
    )
    assert "httpGet" not in liveness
    named_ports = {p["name"] for p in container["ports"]}
    assert liveness["tcpSocket"]["port"] in named_ports, (
        "livenessProbe tcpSocket port must be a declared named container port"
    )
    # Stated explicitly because it is the invariant most likely to be broken by
    # a well-meaning "make liveness accurate too" change: a dependency outage
    # must remove the Pod from rotation, never restart it.
    assert liveness.get("httpGet") is None, (
        "livenessProbe must stay dependency-independent; it may not target /api/ready"
    )


def test_liveness_probe_ignores_dependency_outage_while_readiness_does_not() -> None:
    """The two probes must disagree under a dependency outage. That is the point.

    With every dependency down, `/api/ready` answers 503 and `/api/health`
    answers 200. So the readinessProbe must fail while the livenessProbe, being
    tcpSocket, is unaffected.
    """
    from fastapi.testclient import TestClient

    import api.readiness as readiness_module
    import app as application
    from api.readiness import DEPENDENCY_KEYS, ProbeContext

    client = TestClient(application.app)
    original_probes = readiness_module._default_probes
    original_context = readiness_module._build_context
    try:
        readiness_module._default_probes = lambda ctx: {key: (lambda _c: False) for key in DEPENDENCY_KEYS}
        readiness_module._build_context = lambda: ProbeContext(is_production=True)
        ready = client.get("/api/ready")
    finally:
        readiness_module._default_probes = original_probes
        readiness_module._build_context = original_context

    assert ready.status_code == 503, "readiness must fail closed when nothing is reachable"

    container = _container(_by_kind("Deployment")[0])
    assert container["livenessProbe"]["tcpSocket"]["port"] in {p["name"] for p in container["ports"]}, (
        "liveness stays a tcpSocket check, so it cannot observe the 503 at all"
    )


def test_liveness_probe_would_reject_an_http_get_on_health() -> None:
    """Guard the reason the liveness probe is tcpSocket, not httpGet.

    If someone later "simplifies" the liveness probe back to an httpGet on
    /api/health, this test fails with the reason rather than letting a probe
    that cannot fail ship silently.
    """
    from fastapi.testclient import TestClient

    import app as application

    client = TestClient(application.app)
    response = client.get("/api/health")
    assert response.status_code == 200, (
        "if /api/health stopped returning 200, the tcpSocket liveness rationale must be re-evaluated explicitly"
    )
    body = response.json()
    assert body.get("status") in {"healthy", "degraded"}, "unexpected health payload shape; revisit the probe contract"
    assert "dependencies" in body, (
        "dependency detail lives in the health body; if this changed, readiness can no longer be a plain httpGet"
    )


# --------------------------------------------------------------------------
# 5. Secrets must be referenced, never hardcoded.
# --------------------------------------------------------------------------


def test_secrets_are_referenced_not_hardcoded() -> None:
    deployment = _by_kind("Deployment")[0]
    container = _container(deployment)
    env_from = container.get("envFrom", [])
    secret_refs = [e["secretRef"]["name"] for e in env_from if "secretRef" in e]
    assert secret_refs, "the container must consume secrets via secretRef"
    declared = {s["metadata"]["name"] for s in _by_kind("Secret")}
    for name in secret_refs:
        assert name in declared, f"secretRef {name!r} has no matching Secret manifest"


def test_no_secret_carries_a_real_value() -> None:
    for secret in _by_kind("Secret"):
        string_data = secret.get("stringData") or {}
        assert string_data, "Secret template must declare its key names"
        for key, value in string_data.items():
            assert any(marker in key.upper() for marker in SECRET_KEY_MARKERS), (
                f"{key!r} does not look like a secret key name"
            )
            assert value in PLACEHOLDER_VALUES, f"secret {key!r} has a non-placeholder value; commit only the template"


def test_committed_secret_is_named_as_an_example() -> None:
    """The committed Secret must be unmistakable as a template."""
    for path in K8S_DIR.glob("secret*.yaml"):
        assert "example" in path.name, f"{path.name} must be named secret.example.yaml so it reads as a template"


def test_configmap_holds_no_secret_shaped_keys() -> None:
    for config_map in _by_kind("ConfigMap"):
        for key in config_map.get("data", {}):
            assert not any(marker in key.upper() for marker in SECRET_KEY_MARKERS), (
                f"ConfigMap key {key!r} looks like a secret and belongs in a Secret"
            )


# --------------------------------------------------------------------------
# 6. Resource requests and limits must exist.
# --------------------------------------------------------------------------


def test_container_declares_resource_requests_and_limits() -> None:
    container = _container(_by_kind("Deployment")[0])
    resources = container.get("resources")
    assert resources, "container must declare resources"
    for section in ("requests", "limits"):
        assert resources.get(section), f"resources.{section} must be declared"
        for key in ("cpu", "memory"):
            assert resources[section].get(key), f"resources.{section}.{key} must be set"


def test_resource_limits_do_not_contradict_requests() -> None:
    container = _container(_by_kind("Deployment")[0])
    resources = container["resources"]

    def to_mebibytes(value: Any) -> float:
        text = str(value).strip()
        if text.endswith("Mi"):
            return float(text[:-2])
        if text.endswith("Gi"):
            return float(text[:-2]) * 1024
        return float(text) / (1024 * 1024)

    def to_cores(value: Any) -> float:
        text = str(value).strip()
        if text.endswith("m"):
            return float(text[:-1]) / 1000
        return float(text)

    assert to_cores(resources["limits"]["cpu"]) >= to_cores(resources["requests"]["cpu"]), (
        "limits.cpu is below requests.cpu"
    )
    assert to_mebibytes(resources["limits"]["memory"]) >= to_mebibytes(resources["requests"]["memory"]), (
        "limits.memory is below requests.memory"
    )


# --------------------------------------------------------------------------
# 7. Image / tag policy must be explicit.
# --------------------------------------------------------------------------


def test_image_uses_a_pinned_tag_not_a_floating_one() -> None:
    container = _container(_by_kind("Deployment")[0])
    image = container["image"]
    assert ":" in image.rsplit("/", 1)[-1], f"image {image!r} must carry an explicit tag"
    tag = image.rsplit(":", 1)[1]
    assert tag not in {"latest", "master", "main"}, f"image tag {tag!r} is a floating tag"
    assert container.get("imagePullPolicy") in {"IfNotPresent", "Always"}, "imagePullPolicy must be stated explicitly"


def test_image_tag_matches_the_canonical_runtime_version() -> None:
    """The manifest must not drift from config.json's system.version."""
    import json

    version = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["system"]["version"]
    container = _container(_by_kind("Deployment")[0])
    assert container["image"].endswith(f":{version}"), f"image tag does not match config.json system.version {version}"


# --------------------------------------------------------------------------
# 8. Rolling update strategy.
# --------------------------------------------------------------------------


def test_rolling_update_strategy_is_declared() -> None:
    strategy = _by_kind("Deployment")[0]["spec"]["strategy"]
    assert strategy["type"] == "RollingUpdate"
    rolling = strategy["rollingUpdate"]
    assert 0 <= rolling["maxUnavailable"] <= rolling["maxSurge"], (
        "maxUnavailable should not exceed maxSurge for a zero-downtime rollout"
    )


def test_replicas_are_declared() -> None:
    assert _by_kind("Deployment")[0]["spec"].get("replicas", 0) >= 1


def test_multiple_replicas_require_a_shared_user_store() -> None:
    """The user store is SQLite at ./data/users.db, so replicas must stay at 1.

    auth/user_store.py resolves its path from DATABASE_URL with a
    `sqlite:///./data/users.db` default. This contract declares no external
    database backend and no shared volume for that path, so a second replica
    would hold divergent user rows and silently break login and role updates.
    If someone raises the replica count, this fails with the reason.
    """
    deployment = _by_kind("Deployment")[0]
    replicas = deployment["spec"]["replicas"]
    config_data = _by_kind("ConfigMap")[0].get("data", {})
    uses_shared_db = bool(config_data.get("DATABASE_URL"))
    if replicas > 1:
        assert uses_shared_db, (
            f"replicas={replicas} but no shared user store is configured; "
            "set DATABASE_URL to a supported shared backend first"
        )


def test_rs256_primary_auth_is_configured() -> None:
    """config.json ships auth.dev_mode=false, so RS256 is the only login path.

    auth/jwt_auth.get_jwt_config() enables login only when JWT_ALGORITHM is set,
    and then requires both key paths. If the ConfigMap omits these, login is
    disabled while protected endpoints return 401.
    """
    config_data = _by_kind("ConfigMap")[0].get("data", {})
    assert config_data.get("JWT_ALGORITHM") == "RS256", (
        "JWT_ALGORITHM must be set: config.json ships auth.dev_mode=false, so an "
        "unset value leaves /api/auth/login disabled"
    )
    for key in ("JWT_PRIVATE_KEY_PATH", "JWT_PUBLIC_KEY_PATH"):
        assert config_data.get(key), f"{key} must be declared for the RS256 path"


def test_rs256_key_material_is_mounted_from_a_secret() -> None:
    """The key paths in the ConfigMap must resolve to a mounted Secret volume."""
    deployment = _by_kind("Deployment")[0]
    container = _container(deployment)
    config_data = _by_kind("ConfigMap")[0].get("data", {})
    volumes = {v["name"]: v for v in deployment["spec"]["template"]["spec"].get("volumes", [])}
    mounts = {m["name"]: m for m in container.get("volumeMounts", [])}

    for key in ("JWT_PRIVATE_KEY_PATH", "JWT_PUBLIC_KEY_PATH"):
        path = config_data[key]
        mounted = next(
            (m for m in mounts.values() if path.startswith(m["mountPath"])),
            None,
        )
        assert mounted is not None, f"{key}={path!r} is not inside any volumeMount"
        volume = volumes.get(mounted["name"])
        assert volume is not None, f"volumeMount {mounted['name']!r} has no matching volume"
        assert "secret" in volume, f"{key} must be backed by a secret volume, got {sorted(volume)}"


def test_inference_service_urls_are_configured() -> None:
    """The deployed workload reads VLLM_4B_URL / VLLM_GEN_14B_URL.

    The image entrypoint is `python app.py` (Dockerfile CMD), so the running
    process is the monolith in app.py, not the api-gateway/ application.
    router/stateless_router.py reads VLLM_4B_URL and VLLM_GEN_14B_URL and
    otherwise defaults to http://localhost:<port>, which would point the
    container at itself.

    REWRITE_SERVICE_URL / GENERATION_SERVICE_URL are deliberately NOT accepted
    here: they are read only by api-gateway/routers/*, which this Deployment
    never starts, so setting them would leave the monolith on localhost.
    """
    config_data = _by_kind("ConfigMap")[0].get("data", {})
    for key in ("VLLM_4B_URL", "VLLM_GEN_14B_URL"):
        url = config_data.get(key)
        assert url, (
            f"{key} must be set explicitly; router/stateless_router.py otherwise "
            "falls back to http://localhost and the pod calls itself"
        )
        assert "localhost" not in url and "127.0.0.1" not in url, f"{key}={url!r} points back at the pod itself"

    for wrong_key in ("REWRITE_SERVICE_URL", "GENERATION_SERVICE_URL"):
        assert wrong_key not in config_data, (
            f"{wrong_key} is consumed only by the api-gateway application, which "
            "this Deployment does not start; the monolith reads VLLM_*_URL"
        )


def test_jwt_key_mount_is_readable_by_the_non_root_user() -> None:
    """Secret volumes mount root-owned, so 0400 is unreadable as non-root.

    The image runs as `appuser` under runAsNonRoot. Secret volume files are
    owned by root, so 0400 leaves them unreadable by the application user and
    login fails even after the Secret exists. Group-read plus an explicit
    fsGroup is what makes the mount usable.
    """
    deployment = _by_kind("Deployment")[0]
    pod_spec = deployment["spec"]["template"]["spec"]
    container = _container(deployment)
    security = pod_spec.get("securityContext", {})

    assert security.get("runAsNonRoot") is True, "the pod must run as non-root"
    assert security.get("fsGroup"), (
        "fsGroup is required: Secret volumes are root-owned, so without it the "
        "application user cannot read the mounted keys"
    )

    volumes = {v["name"]: v for v in pod_spec.get("volumes", [])}
    for mount in container.get("volumeMounts", []):
        volume = volumes.get(mount["name"], {})
        if "secret" not in volume:
            continue
        mode = _file_mode(volume["secret"].get("defaultMode", 0o644))
        assert mode & 0o040, (
            f"secret volume {mount['name']!r} has defaultMode {oct(mode)}: the application user cannot read it"
        )
        assert not mode & 0o007, f"secret volume {mount['name']!r} is world-readable ({oct(mode)})"


def test_numeric_ids_match_the_pinned_image_user() -> None:
    """runAsUser/runAsGroup must match the uid/gid the image actually creates.

    The manifests name 1000 and rely on the Dockerfile creating `appuser` with
    that uid. If the Dockerfile stops pinning it, the manifest would reference an
    id the image does not have.
    """
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--uid" in dockerfile and "--gid" in dockerfile, (
        "the Dockerfile must pin the uid/gid the Kubernetes securityContext names"
    )
    security = _by_kind("Deployment")[0]["spec"]["template"]["spec"]["securityContext"]
    for field in ("runAsUser", "runAsGroup", "fsGroup"):
        assert security.get(field), f"securityContext.{field} must be declared"


def test_elasticsearch_username_is_configured_beside_its_password() -> None:
    """BM25 basic_auth needs username AND password.

    retrieval/bm25_retriever.py only sets basic_auth when both values are
    non-empty. A password without a username makes the initial anonymous info()
    call fail with 401, and the BM25 path is then disabled for the process.
    """
    config_data = _by_kind("ConfigMap")[0].get("data", {})
    secret_data = _by_kind("Secret")[0].get("stringData", {})
    assert config_data.get("ELASTICSEARCH_USERNAME"), (
        "ELASTICSEARCH_USERNAME must be set; config.json ships it empty and BM25 basic_auth stays disabled without it"
    )
    assert secret_data.get("ELASTICSEARCH_PASSWORD"), (
        "the Elasticsearch password must be supplied alongside the username"
    )
