#!/bin/sh
# Install Alpine packages from a verified, fully offline package snapshot.
#
# WHY THIS EXISTS
# ---------------
# Digest-pinning `FROM` is not enough to make an image reproducible. An
# unversioned `apk add` resolves the newest available package from the live
# Alpine repositories, so rebuilding the same Git commit on a later day could
# produce a different image -- with a different compiler, different libraries
# and a different MinIO binary, all from an unchanged Git commit.
#
# Two obvious fixes both fail, and the second one fails *silently*:
#
#   * Pinning only the directly requested packages leaves every dependency they
#     pull in resolving from a live index.
#   * Verifying the index digest and handing it to apk with `--repository`
#     looks equivalent and is not. apk expects
#     `$repository/$arch/APKINDEX.tar.gz` with the `.apk` payloads beside it,
#     so an index dumped at the repository root is not a usable repository at
#     all (apk logs "opening ...: No such file or directory"). And
#     `--repository` *supplements* rather than replaces
#     `/etc/apk/repositories`, so the base image's live repositories stay
#     enabled. The install appears to honour the pin while resolving its
#     dependencies from the network. A digest check that cannot bind the
#     resolution is worse than no check, because it looks like a guarantee.
#
# WHAT THIS DOES INSTEAD
# ----------------------
# The caller passes the *complete* transitive closure, every package pinned to
# an exact `name=version-rN` (see the RUN stages in the Dockerfile). That is
# what removes the need to resolve anything here: there is no dependency
# arithmetic left to get wrong, and an incomplete closure cannot pass silently
# because step 4 installs offline.
#
#   1. Fetch APKINDEX.tar.gz for each pinned repository and verify its SHA-256
#      against the recorded pin. Any mismatch fails the build.
#   2. Read the pinned indexes to find which repository each package lives in.
#   3. Stage exactly those `.apk` payloads into a correct
#      `$snapshot/$repo/$arch/` layout.
#   4. Replace /etc/apk/repositories so no network repository remains, then
#      install with `--no-network`. A missing payload fails the build; it can
#      never be fetched from the mutable network index as a fallback.
#   5. Re-check that every package really landed on its pinned version.
#
# The result is a function of the pinned index digests alone.
#
# Deliberate consequence: an Alpine security update in the pinned release now
# FAILS the build (step 1) instead of quietly changing the artifact. Refreshing
# is a deliberate act -- see the Dockerfile comments.
#
# Usage: apk-pin-install.sh <snapshot-dir> <pkg=version> [<pkg=version> ...]

set -eu

snapshot="${1:?snapshot directory required}"
shift
[ "$#" -ge 1 ] || { echo "usage: $0 <snapshot-dir> <pkg=version>..." >&2; exit 1; }

arch="$(uname -m)"

# Select the recorded index digests for this architecture. An architecture with
# no recorded pin must not fall through to a live index.
case "${arch}" in
    x86_64)
        pinned_main="${APK_INDEX_MAIN_X86_64:?APK_INDEX_MAIN_X86_64 is required}"
        pinned_community="${APK_INDEX_COMMUNITY_X86_64:?APK_INDEX_COMMUNITY_X86_64 is required}"
        ;;
    aarch64)
        pinned_main="${APK_INDEX_MAIN_AARCH64:?APK_INDEX_MAIN_AARCH64 is required}"
        pinned_community="${APK_INDEX_COMMUNITY_AARCH64:?APK_INDEX_COMMUNITY_AARCH64 is required}"
        ;;
    *)
        echo "architecture '${arch}' has no recorded APK index pin; record one deliberately" >&2
        exit 1
        ;;
esac

# apk verifies each payload against the checksum recorded in the index it
# resolved from, so a truncated or substituted download fails at install time
# rather than silently installing.
fetch() {
    _url="$1"
    _target="$2"
    _attempt=1
    while [ "${_attempt}" -le "${APK_FETCH_ATTEMPTS:-3}" ]; do
        if wget -q -T 60 -O "${_target}" "${_url}"; then
            return 0
        fi
        rm -f "${_target}"
        _attempt=$((_attempt + 1))
    done
    return 1
}

# ── step 1: fetch and verify each pinned index ───────────────────────────────
for repo in main community; do
    if [ "${repo}" = "main" ]; then
        expected="${pinned_main}"
    else
        expected="${pinned_community}"
    fi
    dest="${snapshot}/${repo}/${arch}/APKINDEX.tar.gz"
    mkdir -p "${snapshot}/${repo}/${arch}"
    if ! fetch "${APK_REPOSITORY:?APK_REPOSITORY is required}/${repo}/${arch}/APKINDEX.tar.gz" "${dest}"; then
        echo "could not download the pinned ${repo}/${arch} index" >&2
        exit 1
    fi
    actual="$(sha256sum "${dest}" | cut -d' ' -f1)"
    if [ "${actual}" != "${expected}" ]; then
        echo "apk index ${repo}/${arch} is not the pinned snapshot:" >&2
        echo "  expected ${expected}" >&2
        echo "  got      ${actual}" >&2
        echo "refresh the APK_INDEX_* digests, the package pins and the base digest deliberately" >&2
        exit 1
    fi
done

# ── step 2: which repository holds each pinned package? ──────────────────────
# Read out of the verified indexes. Probing URLs instead would conflate "not in
# this repository" with "the network hiccuped", and a single timeout on the
# correct repository used to abort a build for a package that was present.
lookup="${snapshot}/pkg-repo.txt"
: > "${lookup}"
for repo in main community; do
    tar -xzOf "${snapshot}/${repo}/${arch}/APKINDEX.tar.gz" APKINDEX \
        | awk -v repo="${repo}" 'BEGIN { RS = "" }
            {
                name = ""; version = ""
                count = split($0, lines, "\n")
                for (i = 1; i <= count; i++) {
                    if (lines[i] ~ /^P:/) name = substr(lines[i], 3)
                    else if (lines[i] ~ /^V:/) version = substr(lines[i], 3)
                }
                if (name != "" && version != "") print name, version, repo
            }' >> "${lookup}"
done

# ── step 3: stage exactly the pinned payloads ────────────────────────────────
for specification in "$@"; do
    name="${specification%%=*}"
    version="${specification#*=}"
    if [ "${name}" = "${specification}" ] || [ -z "${version}" ]; then
        echo "package specification '${specification}' is not pinned to an exact version" >&2
        exit 1
    fi
    repo="$(awk -v n="${name}" -v v="${version}" '$1 == n && $2 == v { print $3; exit }' "${lookup}")"
    if [ -z "${repo}" ]; then
        echo "${name}-${version} is not present in the pinned indexes" >&2
        exit 1
    fi
    if ! fetch "${APK_REPOSITORY}/${repo}/${arch}/${name}-${version}.apk" \
        "${snapshot}/${repo}/${arch}/${name}-${version}.apk"; then
        echo "could not download ${name}-${version}.apk from the pinned ${repo} repository" >&2
        exit 1
    fi
done
rm -f "${lookup}"

# ── step 4: install offline, from the verified snapshot only ─────────────────
# Overwriting this file is what actually removes the mutable network indexes;
# --repository would have left them enabled.
printf 'file://%s/main\nfile://%s/community\n' "${snapshot}" "${snapshot}" > /etc/apk/repositories
apk add --no-network --no-cache "$@"

# ── step 5: confirm the pins actually landed ────────────────────────────────
for specification in "$@"; do
    if ! apk info -e "${specification}" > /dev/null 2>&1; then
        echo "post-install check failed: ${specification} is not installed at its pinned version" >&2
        exit 1
    fi
done