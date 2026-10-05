# MinIO object storage, built from official upstream source.
#
# WHY THIS FILE EXISTS
# --------------------
# MinIO Community Edition became a source-only distribution in October 2025
# (upstream README, "Source-Only Distribution": "We will no longer provide
# pre-compiled binary releases for the community version"). The official
# `minio/minio` repository on Docker Hub was subsequently deleted, so
# `docker compose up` could no longer resolve any `minio/minio:<tag>` reference
# at all, and the previously pinned image would in any case have been frozen
# below the `RELEASE.2025-10-15T17-29-55Z` security fix (CVE-2025-62506).
#
# This image therefore builds MinIO from the official `minio/minio` Git
# repository at a pinned, immutable commit. The source is *not* vendored into
# this repository: MinIO is an external upstream dependency under its own
# AGPLv3 license, and it is fetched at build time from the official remote.
#
# UPSTREAM SOURCE REFERENCE
# ------------------------
#   repository: https://github.com/minio/minio
#   commit:     7aac2a2c5b7c882e68c1ce017d8256be2feea27f
#   date:       2026-02-12T20:18:48Z
#
# That is the final commit on `master` of the archived upstream repository.
# The commit is supplied by `MINIO_COMMIT` (docker-compose.yml) and is required
# to be a full 40-character SHA: a branch, tag or floating reference cannot be
# built from here by accident. The build re-verifies that the checked-out
# HEAD matches the requested SHA before compiling.
#
# The pinned commit's own files govern this build; see the comments on the
# build stage below for the exact upstream contract that is honoured.
#
# LICENSE / PROVENANCE
# --------------------
# MinIO is licensed AGPLv3 by MinIO Inc. This file records dependency
# provenance only: the MinIO source is fetched from the official upstream at
# build time and is not relicensed, redistributed or vendored by this
# repository. Operators are responsible for their own AGPLv3 obligations.

# `golang:1.24-alpine3.22` is pinned to a specific Alpine base, and then pinned
# by digest: a moving tag such as `golang:1.24-alpine` would let the Go patch
# level and the Alpine manifest change underneath the same Git commit, which
# would change the compiler while the Compose image tag and the recorded
# upstream commit stayed identical. The digest below is the multi-arch OCI
# index digest, so the pin stays portable across architectures.
#
# The Go version follows upstream's own go.mod at the pinned commit
# (`go 1.24.0`, `toolchain go1.24.8`).
#
# To update the base deliberately:
#   docker buildx imagetools inspect golang:1.24-alpine3.22
# and replace both the tag and the digest in one commit, noting the reason.
FROM golang:1.24-alpine3.22@sha256:3641e0d9b931dc4f2f185dcd669c4679670e9277c8166a838ddb98a2d4389cb5 AS builder

# Overridable only so the guard test can prove the build reads the upstream
# remote that is documented above; the commit itself is always an argument.
ARG MINIO_REPOSITORY=https://github.com/minio/minio.git

# No default on purpose. An unset or non-immutable reference must fail the
# build instead of silently compiling `master`.
ARG MINIO_COMMIT

# APK INPUTS ARE PINNED AS WELL
# ------------------------------
# Digest-pinning `FROM` is not sufficient on its own. An unversioned
# `apk add` resolves each package from the live Alpine repositories, which
# Alpine documents as mutable (installation selects the newest available
# package), so rebuilding this same commit on a later day could produce a
# different image. Pinning the base digest while leaving the package index
# floating would contradict the reproducibility contract this image claims.
#
# Two mechanisms, both of which fail the build closed:
#
#   1. Every requested package is pinned to an exact `name=version-rN`.
#   2. The package index is itself pinned by SHA-256, per repository and per
#      architecture, and is then handed to apk through a local `file://`
#      snapshot directory. apk therefore resolves the entire transitive
#      closure -- not just the packages named below -- from exactly the bytes
#      that were verified, and there is no remote index left to drift or race.
#
# A consequence worth stating plainly: a security update published into the
# v3.22 branch now *fails this build* rather than silently changing the
# artifact. That is the intended trade, and it makes each refresh a
# reviewable act rather than an accident. To refresh deliberately:
#   curl -fsSLo /tmp/APKINDEX.tar.gz \
#     "${APK_REPOSITORY}/<repo>/$(uname -m)/APKINDEX.tar.gz"
#   sha256sum /tmp/APKINDEX.tar.gz
# then update the four APK_INDEX_* digests, the pinned package versions and
# the `alpine`/`golang` base digest in one commit, noting the reason.
ARG APK_REPOSITORY=https://dl-cdn.alpinelinux.org/alpine/v3.22
ARG APK_INDEX_MAIN_X86_64=9bc67ddd45f47be8508b6e4aa54302685bdb6d968efe8d1f86639f4bffa8aa6a
ARG APK_INDEX_MAIN_AARCH64=143698d71235afff74d1308c71b2821224bac56e002570315d86dae161fe6ce5
ARG APK_INDEX_COMMUNITY_X86_64=548777f7e692b45462a846780d559035d1613633b431cda3ce1fd4602e96f921
ARG APK_INDEX_COMMUNITY_AARCH64=58e2b190e6889394b8e35600cabb70e36aa85e42e3b17817961ac72a989770d1

# `bash`/`perl`/`git` are required by the pinned commit's
# `buildscripts/checkdeps.sh`, which its `make build` target runs first.
RUN set -eu; \
    arch="$(uname -m)"; \
    case "${arch}" in \
        x86_64)  pinned_main="${APK_INDEX_MAIN_X86_64}"; pinned_community="${APK_INDEX_COMMUNITY_X86_64}" ;; \
        aarch64) pinned_main="${APK_INDEX_MAIN_AARCH64}"; pinned_community="${APK_INDEX_COMMUNITY_AARCH64}" ;; \
        *) echo "architecture '${arch}' has no recorded APK index pin; record one deliberately" >&2; exit 1 ;; \
    esac; \
    mkdir -p /apk-snapshot/main /apk-snapshot/community; \
    for repo in main community; do \
        if [ "${repo}" = "main" ]; then expected="${pinned_main}"; else expected="${pinned_community}"; fi; \
        wget -q -O "/apk-snapshot/${repo}/APKINDEX.tar.gz" \
            "${APK_REPOSITORY}/${repo}/${arch}/APKINDEX.tar.gz"; \
        actual="$(sha256sum "/apk-snapshot/${repo}/APKINDEX.tar.gz" | cut -d' ' -f1)"; \
        if [ "${actual}" != "${expected}" ]; then \
            echo "apk index ${repo}/${arch} is not the pinned snapshot: expected ${expected}, got ${actual}" >&2; \
            exit 1; \
        fi; \
    done; \
    apk add --no-cache \
        --repository file:///apk-snapshot/main \
        --repository file:///apk-snapshot/community \
        bash=5.2.37-r0 ca-certificates=20260909-r0 git=2.49.1-r0 make=4.4.1-r3 perl=5.40.4-r0; \
    rm -rf /apk-snapshot

WORKDIR /build

# Fail closed on anything that is not a full 40-character lowercase hex SHA.
# This is the last line of defence; tests/test_container_image_pins.py enforces
# the same rule on the Compose and Dockerfile text.
RUN if [ -z "${MINIO_COMMIT}" ]; then \
        echo "MINIO_COMMIT build arg is required: pass a full 40-character upstream commit SHA" >&2; \
        exit 1; \
    fi; \
    case "${MINIO_COMMIT}" in \
        *[!0-9a-f]*) echo "MINIO_COMMIT must be lowercase hex, got '${MINIO_COMMIT}'" >&2; exit 1;; \
    esac; \
    if [ "${#MINIO_COMMIT}" -ne 40 ]; then \
        echo "MINIO_COMMIT must be a full 40-character SHA, got ${#MINIO_COMMIT} characters" >&2; \
        exit 1; \
    fi

# Fetch exactly the pinned commit. A shallow fetch of the single commit keeps
# the build cheap and means `master` can never be compiled accidentally.
RUN git init --quiet . \
    && git remote add origin "${MINIO_REPOSITORY}" \
    && git fetch --quiet --depth 1 origin "${MINIO_COMMIT}" \
    && git checkout --quiet FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${MINIO_COMMIT}" \
    && echo "checked out upstream minio/minio @ ${MINIO_COMMIT}"

# Build with the pinned commit's own `Makefile` build target rather than with
# hand-written flags. At 7aac2a2c that target is:
#
#   CGO_ENABLED=0 GOOS=$(GOOS) GOARCH=$(GOARCH) \
#     go build -tags kqueue -trimpath --ldflags "$(LDFLAGS)" -o $(PWD)/minio
#
# with `LDFLAGS` produced by upstream's `buildscripts/gen-ldflags.go`, and it
# is gated on `buildscripts/checkdeps.sh` (see the apk line above).
RUN make build \
    && test -x /build/minio \
    && /build/minio --version


# `alpine:3.22.6` is pinned to a patch version and then pinned by digest, for
# the same reason as the builder above: the runtime base must not move under the
# same Git commit. Multi-arch OCI index digest.
#
# To update deliberately:
#   docker buildx imagetools inspect alpine:3.22.6
FROM alpine:3.22.6@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8

# Same APK snapshot contract as the builder stage above; ARG scope is per
# stage, so the pins are restated here rather than inherited.
ARG APK_REPOSITORY=https://dl-cdn.alpinelinux.org/alpine/v3.22
ARG APK_INDEX_MAIN_X86_64=9bc67ddd45f47be8508b6e4aa54302685bdb6d968efe8d1f86639f4bffa8aa6a
ARG APK_INDEX_MAIN_AARCH64=143698d71235afff74d1308c71b2821224bac56e002570315d86dae161fe6ce5
ARG APK_INDEX_COMMUNITY_X86_64=548777f7e692b45462a846780d559035d1613633b431cda3ce1fd4602e96f921
ARG APK_INDEX_COMMUNITY_AARCH64=58e2b190e6889394b8e35600cabb70e36aa85e42e3b17817961ac72a989770d1

# ca-certificates: TLS trust store for outbound S3/HTTPS calls.
# curl: the canonical Compose healthcheck probes the server with
#      `curl -f http://localhost:9000/minio/health/live`, so the runtime image
#      must still ship curl for that contract to keep working.
# tzdata: MinIO resolves bucket/object timestamps through the local zoneinfo.
RUN set -eu; \
    arch="$(uname -m)"; \
    case "${arch}" in \
        x86_64)  pinned_main="${APK_INDEX_MAIN_X86_64}"; pinned_community="${APK_INDEX_COMMUNITY_X86_64}" ;; \
        aarch64) pinned_main="${APK_INDEX_MAIN_AARCH64}"; pinned_community="${APK_INDEX_COMMUNITY_AARCH64}" ;; \
        *) echo "architecture '${arch}' has no recorded APK index pin; record one deliberately" >&2; exit 1 ;; \
    esac; \
    mkdir -p /apk-snapshot/main /apk-snapshot/community; \
    for repo in main community; do \
        if [ "${repo}" = "main" ]; then expected="${pinned_main}"; else expected="${pinned_community}"; fi; \
        wget -q -O "/apk-snapshot/${repo}/APKINDEX.tar.gz" \
            "${APK_REPOSITORY}/${repo}/${arch}/APKINDEX.tar.gz"; \
        actual="$(sha256sum "/apk-snapshot/${repo}/APKINDEX.tar.gz" | cut -d' ' -f1)"; \
        if [ "${actual}" != "${expected}" ]; then \
            echo "apk index ${repo}/${arch} is not the pinned snapshot: expected ${expected}, got ${actual}" >&2; \
            exit 1; \
        fi; \
    done; \
    apk add --no-cache \
        --repository file:///apk-snapshot/main \
        --repository file:///apk-snapshot/community \
        ca-certificates=20260909-r0 curl=8.14.1-r3 tzdata=2026d-r0; \
    rm -rf /apk-snapshot; \
    addgroup -S -g 1000 minio; \
    adduser -S -u 1000 -G minio minio

COPY --from=builder /build/minio /usr/local/bin/minio

# Provenance labels: the exact upstream commit this binary was compiled from.
# OCI source labels are the machine-readable record; the digest of the built
# image plus this label is what makes the artifact auditable.
ARG MINIO_COMMIT
ARG MINIO_COMMIT_DATE=2026-02-12T20-18-48Z
LABEL org.opencontainers.image.title="MinIO (built from official upstream source)" \
      org.opencontainers.image.description="MinIO object storage compiled from github.com/minio/minio at a pinned commit. MinIO is an external AGPLv3 dependency; this image does not vendor or relicense it." \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.vendor="MinIO Inc <dev@min.io> (upstream source); image assembled by this repository" \
      org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="${MINIO_COMMIT}" \
      io.minio.upstream.commit="${MINIO_COMMIT}" \
      io.minio.upstream.commit-date="${MINIO_COMMIT_DATE}"

# Create and own the data directory *before* declaring the volume. Docker's
# legacy builder discards filesystem changes made after a VOLUME instruction,
# so chowning afterwards is silently dropped when the image is built with
# `DOCKER_BUILDKIT=0`; a fresh named volume then inherits root ownership and
# the UID 1000 process cannot initialise it. Ordering it here makes the result
# identical under both builders.
RUN mkdir -p /minio_data && chown minio:minio /minio_data

# The Compose service mounts the `minio-data` named volume here. It matches the
# canonical Compose contract and is unchanged from the previous image.
VOLUME ["/minio_data"]

# S3 API port. The console port (9001) stays declared in Compose.
EXPOSE 9000

# Drop privileges before serving. The previous public image ran as UID 0; this
# one runs as an unprivileged user, so a container escape does not start as
# root. The uid/gid (1000) match the root Dockerfile's application user.
#
# UPGRADE NOTE: a *freshly created* `minio-data` volume inherits the ownership
# established above, so new deployments need nothing extra. A volume created by
# an older root-running image is owned by root and must be handed over once:
#   docker run --rm -v <project>_minio-data:/minio_data alpine:3.22.6 \
#     chown -R 1000:1000 /minio_data
WORKDIR /minio_data

USER minio

# No ENTRYPOINT on purpose. The canonical Compose service sets
# `command: ["minio", "server", "/minio_data", "--console-address", ":9001"]`,
# and with no ENTRYPOINT that list is the container's argv verbatim — the same
# behaviour the previously pulled image provided via its entrypoint wrapper.
# This CMD mirrors that command so a bare `docker run` behaves identically to
# `docker compose up`.
CMD ["minio", "server", "/minio_data", "--console-address", ":9001"]