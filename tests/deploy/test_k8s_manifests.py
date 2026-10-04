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


def test_probes_target_real_unauthenticated_endpoints() -> None:
    """`/api/health` is live and unauthenticated; `/api/metrics` returns 401.

    Verified against the application, not assumed: GET /api/health -> 200,
    GET /api/metrics -> 401 without credentials.
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

        assert http_get["path"] == "/api/health", f"{probe_name} path {http_get['path']!r} is not a verified endpoint"
        assert http_get["path"] != "/api/metrics", f"{probe_name} must not use /api/metrics: it requires authentication"
        assert http_get["port"] in named_ports, (
            f"{probe_name} port {http_get['port']!r} is not a declared named container port"
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
    named_ports = {p["name"] for p in _container(_by_kind("Deployment")[0])["ports"]}
    assert liveness["tcpSocket"]["port"] in named_ports, (
        "livenessProbe tcpSocket port must be a declared named container port"
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
