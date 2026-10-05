"""Deterministic supply-chain guard for the canonical container image references.

Why this file exists
--------------------
``docker-compose.yml`` used to pull ``image: minio/minio:latest``. That
reference had two defects at once:

1. **Mutable.** ``:latest`` is not tied to the Git commit, so the same commit
   can deploy different bytes on different days.
2. **Dead.** MinIO Community Edition became a source-only distribution
   (upstream README, "Source-Only Distribution"), and the official
   ``minio/minio`` Docker Hub repository was subsequently deleted
   (``GET /v2/repositories/minio/minio/tags`` returns ``object not found``). So
   the reference could not be resolved at all -- the failure mode was a broken
   ``docker compose up``, not merely drift.

The canonical deployment therefore builds MinIO from the official upstream
source at an immutable commit (``deploy/minio/Dockerfile``). These tests pin
that contract so neither half can regress silently.

Scope: the **canonical** deployment surface only. ``docker-compose.gpu.yml`` /
``.cpu.yml`` / ``.microservices.yml`` / ``.observability.yml`` are overlays that
are not automatically pulled into this issue.

The checks are static and deterministic: they read text and parse YAML. Nothing
here contacts a registry, so no test result may be reported as evidence that a
container image was actually built or that MinIO was actually run.
"""

from __future__ import annotations

import os
import re

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CANONICAL_COMPOSE = os.path.join(REPO_ROOT, "docker-compose.yml")
MINIO_DOCKERFILE = os.path.join(REPO_ROOT, "deploy", "minio", "Dockerfile")

#: The upstream commit the canonical deployment is pinned to. Recorded here as
#: well as in Compose so that changing one without the other fails a test.
PINNED_MINIO_COMMIT = "7aac2a2c5b7c882e68c1ce017d8256be2feea27f"

#: Uppercase/lowercase and the named floating references that must never be
#: used as a *source* reference for a build.
FLOATING_SOURCE_REFS = ("master", "main", "latest", "HEAD")

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _instructions(path: str) -> str:
    """Dockerfile text with comment lines removed.

    Guards assert on instructions, not on prose: this file documents *why* the
    Dockerfile has no ENTRYPOINT, and that sentence must not satisfy (or break)
    an ENTRYPOINT check.
    """
    return "\n".join(line for line in _read(path).splitlines() if not line.lstrip().startswith("#"))


def _compose() -> dict:
    return yaml.safe_load(_read(CANONICAL_COMPOSE))


@pytest.fixture(scope="module")
def minio_service() -> dict:
    services = _compose().get("services", {})
    assert "minio" in services, "the canonical Compose file must keep its MinIO service"
    return services["minio"]


# ── 1/2. no mutable or dead public MinIO image reference ────────────────────
def test_canonical_compose_has_no_latest_tag():
    """No canonical service may carry a floating ``:latest`` reference.

    ``:latest`` means the deployed artifact is not a function of the Git commit.
    """
    offenders = {
        name: service.get("image")
        for name, service in _compose().get("services", {}).items()
        if isinstance(service.get("image"), str) and ":latest" in service["image"]
    }
    assert not offenders, f"canonical services use a mutable :latest tag: {offenders}"


def test_canonical_compose_does_not_reference_public_minio_image():
    """`minio/minio:*` is unusable: the Docker Hub repository is deleted.

    Guards against "fixing" the missing image by pointing at the public MinIO
    image again (directly or via a registry-qualified equivalent).
    """
    content = _read(CANONICAL_COMPOSE)
    offenders = re.findall(r"^\s*image:\s*(\S*minio/minio\S*)\s*$", content, re.MULTILINE)
    assert not offenders, (
        f"canonical Compose must not reference the deleted public MinIO image: {offenders}. "
        "Build from deploy/minio/Dockerfile at a pinned upstream commit instead."
    )


def test_minio_image_reference_is_repository_owned(minio_service):
    """The service image name is built locally, not pulled from a public registry."""
    image = minio_service.get("image", "")
    assert image.startswith("beauty-rag-minio:"), (
        f"expected a repository-owned MinIO image name, got {image!r}; the tag must stay "
        "auditable back to the pinned upstream commit"
    )
    assert "minio/minio" not in image


# ── 3/4. the source reference is immutable and full-length ──────────────────
def test_minio_build_arg_is_a_full_commit_sha(minio_service):
    """A short/abbreviated SHA is not an immutable reference."""
    args = minio_service.get("build", {}).get("args", {})
    commit = args.get("MINIO_COMMIT")
    assert commit is not None, "the MinIO build must receive MINIO_COMMIT"
    assert FULL_SHA_RE.match(str(commit)), (
        f"MINIO_COMMIT must be a full 40-character lowercase commit SHA, got {commit!r}"
    )


@pytest.mark.parametrize("floating", FLOATING_SOURCE_REFS)
def test_minio_commit_is_not_a_floating_reference(floating):
    """master/main/latest/HEAD are rejected case-insensitively.

    A future "fix" that sets MINIO_COMMIT to a branch must fail here rather than
    silently reintroducing drift.
    """
    commit = _compose()["services"]["minio"]["build"]["args"]["MINIO_COMMIT"]
    assert commit.lower() != floating, f"MINIO_COMMIT must not be the floating reference {floating!r}"
    assert not commit.lower().startswith(floating), f"MINIO_COMMIT must not start with {floating!r}"


def test_pinned_commit_matches_the_documented_source(minio_service):
    """Compose's pin and this guard's recorded pin must not drift apart."""
    assert minio_service["build"]["args"]["MINIO_COMMIT"] == PINNED_MINIO_COMMIT, (
        "the canonical Compose pin changed; update PINNED_MINIO_COMMIT and the "
        "deploy/minio/Dockerfile provenance comment together, and record why"
    )


def test_local_image_tag_tracks_the_pinned_commit(minio_service):
    """The local image tag is a short form of the pinned commit, not `latest`."""
    tag = minio_service["image"].split(":", 1)[1]
    assert tag == PINNED_MINIO_COMMIT[:8], (
        f"image tag {tag!r} does not track the pinned commit prefix; it must stay auditable "
        f"to {PINNED_MINIO_COMMIT[:8]}"
    )


# ── 5. the Dockerfile actually builds that immutable ref ────────────────────
def test_dockerfile_exists_and_is_a_multi_stage_build():
    """A separate, minimal Dockerfile -- not a vendored MinIO source tree."""
    assert os.path.isfile(MINIO_DOCKERFILE), "deploy/minio/Dockerfile must exist"
    content = _read(MINIO_DOCKERFILE)
    assert content.count("FROM ") >= 2, "expected a multi-stage build (builder + runtime)"
    assert "AS builder" in content, "the build stage must be named so the runtime can COPY from it"


def test_dockerfile_uses_the_minio_commit_arg():
    """The Dockerfile builds from ``$MINIO_COMMIT``, not a hardcoded/floating ref."""
    content = _read(MINIO_DOCKERFILE)
    assert "ARG MINIO_COMMIT" in content, "the Dockerfile must declare the MINIO_COMMIT build arg"
    assert "MINIO_REPOSITORY" in content, (
        "the fetch remote must be an ARG so the guard can assert it is the official upstream"
    )
    assert "github.com/minio/minio" in content, "the source must come from the official upstream"


@pytest.mark.parametrize("floating", FLOATING_SOURCE_REFS)
def test_dockerfile_has_no_floating_source_reference(floating):
    """No `--branch master`, `checkout main`, `git clone ...latest` style default."""
    content = _read(MINIO_DOCKERFILE)
    # A build arg named after a floating ref, or a clone/checkout defaulting to one.
    offenders = re.findall(rf"(?:--branch|--depth 1 origin|-b|-B)\s*{floating}\b", content, re.IGNORECASE)
    assert not offenders, f"the Dockerfile must not build from a floating ref {floating!r}: {offenders}"


def test_dockerfile_validates_the_commit_shape():
    """The build fails closed on a missing or non-SHA reference.

    Without this, `docker build` with no arg could fall through to a branch and
    silently produce a different binary for the same Compose file.
    """
    content = _read(MINIO_DOCKERFILE)
    assert "-ne 40" in content, "the Dockerfile must reject any reference that is not 40 characters"
    assert 'test "$(git rev-parse HEAD)"' in content or "git rev-parse HEAD" in content, (
        "the build must verify the checked-out HEAD equals the requested SHA"
    )


def test_dockerfile_does_not_vendor_minio_source():
    """MinIO stays an external upstream dependency; no code is copied in."""
    content = _read(MINIO_DOCKERFILE)
    assert "COPY . /" not in content and "COPY ./ /" not in content, (
        "the MinIO image must not bake this repository's source tree into it"
    )
    assert "go install github.com/minio/minio@" not in content, (
        "the build must pin a commit; `go install ...@latest` is not reproducible"
    )


def test_dockerfile_base_images_are_not_floating():
    """Base images must carry an explicit, non-moving tag.

    They are literals rather than build ARGs on purpose: a literal cannot be
    overridden from the Compose file or a CI flag, so there is no path by which
    the builder or runtime base silently changes under the same Git commit.

    Two acceptable pin forms are recognised, both naming a concrete Alpine
    release: ``golang:1.24-alpine3.22`` (Go series + Alpine minor) and
    ``alpine:3.22.6`` (Alpine patch). A bare ``golang:1.24-alpine`` or
    ``alpine:3.22`` would both move.
    """
    base_images = re.findall(r"^FROM\s+(\S+)", _instructions(MINIO_DOCKERFILE), re.MULTILINE)
    assert base_images, "expected at least one FROM instruction"
    for image in base_images:
        assert ":" in image, f"base image {image!r} has no tag and would resolve to a moving default"
        tag = image.rsplit(":", 1)[1]
        assert tag != "latest", f"floating base image: {image}"
        pinned_to_alpine = re.search(r"alpine3\.\d+", tag) or re.search(r"\d+\.\d+\.\d+", tag)
        assert pinned_to_alpine, (
            f"base image {image!r} must pin a concrete Alpine release (e.g. alpine3.22 or 3.22.6); {tag!r} still moves"
        )


# ── 6/7/8. the MinIO runtime contract is preserved ──────────────────────────
def test_minio_data_volume_is_preserved(minio_service):
    """Object data must stay on the same named volume as before."""
    volumes = minio_service.get("volumes", [])
    assert any("minio-data" in str(v) for v in volumes), f"the minio-data volume is gone: {volumes}"
    assert any("/minio_data" in str(v) for v in volumes), f"the /minio_data mount point changed: {volumes}"
    assert "minio-data" in _compose().get("volumes", {}), "the minio-data named volume declaration is gone"


def test_minio_auth_env_contract_is_preserved(minio_service):
    """The fail-fast root credential contract must not be weakened."""
    env = minio_service.get("environment", [])
    joined = " ".join(env) if isinstance(env, list) else str(env)
    assert "MINIO_ROOT_USER=${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY must be set}" in joined
    assert "MINIO_ROOT_PASSWORD=${MINIO_SECRET_KEY:?MINIO_SECRET_KEY must be set}" in joined


def test_minio_ports_command_and_healthcheck_are_preserved(minio_service):
    """Ports, server command and the curl-based healthcheck stay byte-identical.

    The healthcheck shells out to ``curl``; if the rebuilt runtime image ever
    stopped shipping curl, this contract would silently degrade to
    "unhealthy forever" rather than fail loudly.
    """
    assert minio_service.get("expose") == ["9001"], "the console port exposure changed"
    assert minio_service.get("command") == [
        "minio",
        "server",
        "/minio_data",
        "--console-address",
        ":9001",
    ], "the MinIO server command changed; /api/media presigned URLs depend on it"

    healthcheck = minio_service.get("healthcheck", {})
    assert healthcheck.get("test") == ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"], (
        "the healthcheck contract changed"
    )
    assert healthcheck.get("interval") == "30s"
    assert healthcheck.get("timeout") == "20s"
    assert healthcheck.get("retries") == 3
    assert minio_service.get("restart") == "unless-stopped"
    assert "rag-network" in (minio_service.get("networks") or []), "the MinIO service left rag-network"


def test_runtime_image_ships_curl_for_the_healthcheck():
    """The rebuilt runtime stage must install curl, or the healthcheck cannot run."""
    content = _read(MINIO_DOCKERFILE)
    runtime_stage = content.split("AS builder", 1)[1]
    assert "curl" in runtime_stage, "the runtime stage must install curl for the Compose healthcheck"
    assert "/usr/local/bin/minio" in content, (
        "the minio binary must be on PATH so the Compose command's bare `minio` argv resolves"
    )
    assert "ENTRYPOINT" not in _instructions(MINIO_DOCKERFILE), (
        "an ENTRYPOINT would prefix Compose's `command` argv and turn `minio minio server ...` into a broken invocation"
    )


# ── scope guard: overlays are not silently absorbed ─────────────────────────
def test_non_canonical_overlays_are_not_in_scope():
    """Only the canonical file is audited here; record that explicitly.

    If a future change adds MinIO to an overlay, that is a different dependency
    surface and must be reviewed on its own rather than inherited from this
    issue's green tests.
    """
    overlays = [
        "docker-compose.gpu.yml",
        "docker-compose.cpu.yml",
        "docker-compose.microservices.yml",
        "docker-compose.observability.yml",
    ]
    for name in overlays:
        path = os.path.join(REPO_ROOT, name)
        if not os.path.isfile(path):
            continue
        content = _read(path)
        assert "minio/minio" not in content, (
            f"{name} references the deleted public MinIO image; that overlay is outside the "
            "canonical-deployment scope of this guard and needs its own change"
        )
