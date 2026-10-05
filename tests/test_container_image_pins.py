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

#: An apk package pinned to an exact Alpine version, e.g. ``curl=8.14.1-r3``.
PINNED_APK_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*=[0-9][^\s;]*$")

#: Options whose value is the following token rather than a package name.
APK_OPTIONS_WITH_VALUE = ("--repository", "--repositories-file", "--cache-dir")

#: Architectures the APK index digests are recorded for. The base images are
#: multi-arch OCI index digests, so the image builds on any of these and the
#: package snapshot has to be pinned for each of them.
PINNED_ARCHITECTURES = ("x86_64", "aarch64")

#: Repositories whose package index is pinned.
PINNED_APK_REPOSITORIES = ("main", "community")


def _stages() -> list[tuple[str, str]]:
    """(stage-name, unfolded-instruction-text) for every Dockerfile stage."""
    text = _joined_instructions(MINIO_DOCKERFILE)
    marker = "AS builder"
    return [("builder", text.split(marker, 1)[0]), ("runtime", text.split(marker, 1)[1])]


def _apk_add_invocations(stage: str) -> list[str]:
    """The argument list of each ``apk add`` command in a stage.

    The `apk add` prefix itself is dropped so callers see only packages and
    options.
    """
    invocations = []
    for fragment in re.split(r";|&&", stage):
        if "apk add" in fragment:
            invocations.append(fragment.split("apk add", 1)[1])
    return invocations


def _apk_packages(invocation: str) -> list[str]:
    """Package names in an ``apk add`` command, with options and values removed."""
    packages: list[str] = []
    expecting_value = False
    for token in invocation.split():
        if expecting_value:
            expecting_value = False
            continue
        if token.startswith("-"):
            expecting_value = token in APK_OPTIONS_WITH_VALUE
            continue
        packages.append(token)
    return packages


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


def _joined_instructions(path: str) -> str:
    """Instructions with ``\\`` line continuations folded onto one line.

    The apk installs are multi-line ``RUN`` bodies, so any guard that has to
    reason about a whole command has to see it unfolded.
    """
    folded: list[str] = []
    buffer = ""
    for line in _instructions(path).splitlines():
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        folded.append(buffer + stripped)
        buffer = ""
    if buffer:
        folded.append(buffer)
    return "\n".join(folded)


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


def test_dockerfile_base_images_are_pinned_by_digest():
    """Every base image must be an immutable digest, not merely a non-moving tag.

    A version tag is not enough for this contract. ``golang:1.24-alpine3.22``
    still moves: the Go patch level and the Alpine manifest are both rebuilt
    upstream under the same tag, so the compiler can change while the Compose
    image tag and the recorded MinIO commit stay identical. That is exactly the
    drift this issue exists to remove, one layer down.
    """
    base_images = re.findall(r"^FROM\s+(\S+)", _instructions(MINIO_DOCKERFILE), re.MULTILINE)
    assert base_images, "expected at least one FROM instruction"
    for image in base_images:
        assert "@sha256:" in image, (
            f"base image {image!r} is not digest-pinned; a version tag can still be rebuilt "
            "upstream and change the artifact without changing the Git commit"
        )
        digest = image.rsplit("@", 1)[1]
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest), f"malformed digest in {image!r}"
        assert ":latest" not in image, f"floating base image: {image}"

    # The digest must accompany a human-readable tag, so the pin is auditable
    # and an operator can see what they are updating away from.
    for image in base_images:
        assert image.split("@", 1)[0].count(":"), f"base image {image!r} should keep a readable tag next to its digest"


# ── apk inputs are pinned too, not only the base images ────────────────────
def test_apk_packages_are_pinned_to_exact_versions():
    """Every `apk add` package must carry an exact `=version-rN`.

    Digest-pinning `FROM` does not pin what the build installs. An unversioned
    `apk add` resolves the newest available package from the live Alpine
    repositories, so rebuilding this same commit on a later day could produce a
    different image -- the same drift one layer down.
    """
    found_any = False
    for stage_name, stage in _stages():
        for invocation in _apk_add_invocations(stage):
            packages = _apk_packages(invocation)
            assert packages, f"{stage_name} has an `apk add` with no packages: {invocation!r}"
            for package in packages:
                assert PINNED_APK_PACKAGE_RE.match(package), (
                    f"{stage_name} installs unpinned apk package {package!r}; a version tag can be "
                    "superseded upstream and change the artifact without changing the Git commit"
                )
            found_any = True
    assert found_any, "expected at least one `apk add` to check"


def test_apk_resolution_uses_a_verified_local_snapshot():
    """The package *index* must be pinned, or the transitive closure still floats.

    Pinning only the directly requested packages still leaves every dependency
    they pull in resolving from a live index. The guard therefore requires that
    the index is verified by digest and then served to apk over `file://`, so
    apk resolves the whole closure from bytes that were checked.
    """
    for stage_name, stage in _stages():
        invocations = _apk_add_invocations(stage)
        if not invocations:
            continue
        for invocation in invocations:
            repositories = re.findall(r"--repository\s+(\S+)", invocation)
            assert repositories, (
                f"{stage_name} resolves packages from apk's default repositories; pass "
                "--repository explicitly so the index in use is the pinned one"
            )
            for repository in repositories:
                assert repository.startswith("file://"), (
                    f"{stage_name} resolves from {repository!r}, which is not the verified local "
                    "snapshot; a network index can move between the digest check and the install"
                )
        assert "sha256sum" in stage, f"{stage_name} must verify the index digest before installing"
        assert "exit 1" in stage, f"{stage_name} must fail closed on an unpinned index"


def test_apk_index_digests_are_recorded_for_every_supported_architecture():
    """Both stages pin an index digest per repository and architecture.

    The base images are multi-arch OCI index digests, so the image builds on
    more than one architecture. A pin recorded for only the build host's
    architecture would leave the others silently unpinned.
    """
    content = _instructions(MINIO_DOCKERFILE)
    declared = re.findall(r"^ARG\s+(APK_INDEX_\w+)=(\S+)", content, re.MULTILINE)
    assert declared, "the Dockerfile must record the APK index digests as build args"

    recorded = {name: digest for name, digest in declared}
    expected_names = {
        f"APK_INDEX_{repository.upper()}_{architecture.upper()}"
        for repository in PINNED_APK_REPOSITORIES
        for architecture in PINNED_ARCHITECTURES
    }
    missing = expected_names - set(recorded)
    assert not missing, f"APK inputs are not pinned for every repository/architecture: {sorted(missing)}"

    for name, digest in recorded.items():
        assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{name} is not a well-formed sha256: {digest!r}"

    # An architecture the pins do not cover must not fall through to a live
    # index: the `case` in the RUN bodies has to refuse it instead.
    for stage_name, stage in _stages():
        if not _apk_add_invocations(stage):
            continue
        assert "uname -m" in stage, f"{stage_name} must select the pinned digest by architecture"
        for architecture in PINNED_ARCHITECTURES:
            assert f"APK_INDEX_MAIN_{architecture.upper()}" in stage, (
                f"{stage_name} does not read the x86_64/aarch64 pins it declares"
            )


def test_apk_repository_is_pinned_to_a_named_release():
    """The repository URL must name a release branch, not a floating path.

    `dl-cdn.alpinelinux.org/alpine/latest` is a moving target; the digest check
    is only meaningful against a named release.
    """
    content = _instructions(MINIO_DOCKERFILE)
    urls = re.findall(r"^ARG\s+APK_REPOSITORY=(\S+)", content, re.MULTILINE)
    assert urls, "the Dockerfile must record the Alpine repository URL as a build arg"
    for url in urls:
        assert re.fullmatch(r"https://[\w.-]+/alpine/v\d+\.\d+", url), (
            f"APK_REPOSITORY {url!r} must be a named Alpine release (…/alpine/vMAJOR.MINOR)"
        )


# ── data directory ownership must survive both builders ────────────────────
def test_data_directory_is_owned_before_the_volume_is_declared():
    """`mkdir` + `chown` must precede `VOLUME`.

    Docker's legacy builder discards filesystem changes made after a VOLUME
    instruction, so a chown placed after VOLUME is silently dropped when the
    image is built with `DOCKER_BUILDKIT=0`. A fresh named volume then inherits
    root ownership and the UID 1000 process cannot initialise it.
    """
    instructions = _instructions(MINIO_DOCKERFILE)
    volume_positions = [m.start() for m in re.finditer(r"^VOLUME\b", instructions, re.MULTILINE)]
    assert volume_positions, "the runtime stage must still declare the /minio_data volume"

    for match in re.finditer(r"chown\s+minio:minio\s+/minio_data", instructions):
        assert match.start() < min(volume_positions), (
            "chown minio:minio /minio_data must come before VOLUME; the legacy builder discards "
            "changes made after VOLUME, leaving /minio_data root-owned"
        )


# ── the local image must actually be rebuilt when the recipe changes ───────
def test_minio_service_rebuilds_when_the_dockerfile_changes(minio_service):
    """`docker compose up -d` must not silently reuse a stale local tag.

    The image tag encodes only the upstream MinIO commit, so it does not change
    when the Dockerfile does. Without an explicit build policy a host that
    already has `beauty-rag-minio:<tag>` reuses the cached image and skips the
    digest pins, the non-root runtime, and any later deliberate refresh.
    """
    assert minio_service.get("pull_policy") == "build", (
        "the MinIO service must set `pull_policy: build`; the image tag is derived from the "
        "upstream commit, so `docker compose up -d` would otherwise reuse a cached image and "
        "silently skip the current recipe"
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


def test_runtime_image_drops_root_privileges():
    """The storage service must not run as UID 0.

    The previous public image ran as root. This image creates an unprivileged
    `minio` user, so it must actually select it: creating the account and then
    leaving `USER` unset would be hardening theatre that grants root and implies
    otherwise.
    """
    instructions = _instructions(MINIO_DOCKERFILE)
    runtime_stage = instructions.split("AS builder", 1)[1]
    user_directives = re.findall(r"^USER\s+(\S+)", runtime_stage, re.MULTILINE)
    assert user_directives, (
        "the runtime stage must set USER; MinIO is network-facing storage and does not need container root"
    )
    assert user_directives[-1] not in ("root", "0"), "the runtime stage must not run as root"
    assert "adduser" in runtime_stage, "the unprivileged account the USER directive selects must exist"
    # The data mount must be writable by that account, otherwise the switch
    # trades a privilege problem for a broken volume.
    assert "chown minio:minio /minio_data" in runtime_stage, (
        "/minio_data must be owned by the unprivileged user; a fresh named volume inherits "
        "this ownership and an unwritable data directory would break /api/media"
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
