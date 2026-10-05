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

# `golang:1.24-alpine3.22` is pinned to a specific Alpine base on purpose:
# the moving `golang:1.24-alpine` tag would change its base underneath the
# same Git commit. The Go version follows upstream's own go.mod at the pinned
# commit (`go 1.24.0`, `toolchain go1.24.8`).
FROM golang:1.24-alpine3.22 AS builder

# Overridable only so the guard test can prove the build reads the upstream
# remote that is documented above; the commit itself is always an argument.
ARG MINIO_REPOSITORY=https://github.com/minio/minio.git

# No default on purpose. An unset or non-immutable reference must fail the
# build instead of silently compiling `master`.
ARG MINIO_COMMIT

# `bash`/`perl`/`git` are required by the pinned commit's
# `buildscripts/checkdeps.sh`, which its `make build` target runs first.
RUN apk add --no-cache bash ca-certificates git make perl

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


# `alpine:3.22.6` is pinned to a patch version rather than `3.22`/`latest`.
FROM alpine:3.22.6

# ca-certificates: TLS trust store for outbound S3/HTTPS calls.
# curl: the canonical Compose healthcheck probes the server with
#      `curl -f http://localhost:9000/minio/health/live`, so the runtime image
#      must still ship curl for that contract to keep working.
# tzdata: MinIO resolves bucket/object timestamps through the local zoneinfo.
RUN apk add --no-cache ca-certificates curl tzdata \
    && addgroup -S -g 1000 minio \
    && adduser -S -u 1000 -G minio minio

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

# The Compose service mounts the `minio-data` named volume here. It matches the
# canonical Compose contract and is unchanged from the previous image.
VOLUME ["/minio_data"]

# S3 API port. The console port (9001) stays declared in Compose.
EXPOSE 9000

WORKDIR /minio_data

# No ENTRYPOINT on purpose. The canonical Compose service sets
# `command: ["minio", "server", "/minio_data", "--console-address", ":9001"]`,
# and with no ENTRYPOINT that list is the container's argv verbatim — the same
# behaviour the previously pulled image provided via its entrypoint wrapper.
# This CMD mirrors that command so a bare `docker run` behaves identically to
# `docker compose up`.
CMD ["minio", "server", "/minio_data", "--console-address", ":9001"]