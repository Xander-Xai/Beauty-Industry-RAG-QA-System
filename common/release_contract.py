"""Admission of externally reviewed evidence into the industrial RAG pipeline.

An index cannot approve its own content. The manifest is an independently
reviewed, immutable deployment input. It is not a legal determination.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_EPOCH = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def industrial_enabled() -> bool:
    return os.environ.get("RAG_RELEASE_PROFILE", "").strip() == "industrial"


def china_today() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


def _string(value) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= 512


def _mask(value) -> bool:
    return type(value) is int and 0 <= value <= 0xFFFFFFFF


def _day(value) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Release applicability dates must be ISO dates")
    return date.fromisoformat(value)


@dataclass(frozen=True)
class ApprovedRecord:
    doc_id: str
    content_sha256: str
    role_mask: int
    dept_mask: int
    valid_from: date
    valid_until: date
    source_ref: str
    review_ref: str


@dataclass(frozen=True)
class ReleaseManifest:
    epoch: str
    digest: str
    valid_from: date
    valid_until: date
    records: Mapping[str, ApprovedRecord]

    def active(self, today: date) -> bool:
        return self.valid_from <= today <= self.valid_until

    def accepts(self, candidate, today: date) -> bool:
        metadata = candidate.metadata
        record_id = metadata.get("chunk_id") or metadata.get("image_id")
        if not isinstance(record_id, str):
            return False
        entry = self.records.get(record_id)
        content = candidate.content
        return bool(
            entry is not None
            and self.active(today)
            and entry.valid_from <= today <= entry.valid_until
            and candidate.doc_id == entry.doc_id
            and isinstance(content, str)
            and content.strip()
            and hashlib.sha256(content.encode("utf-8")).hexdigest() == entry.content_sha256
            and metadata.get("status") == "active"
            and metadata.get("doc_version_epoch") == self.epoch
            and _mask(metadata.get("role_mask"))
            and _mask(metadata.get("dept_mask"))
            and metadata["role_mask"] == entry.role_mask
            and metadata["dept_mask"] == entry.dept_mask
        )


@lru_cache(maxsize=2)
def load_manifest(path: str, expected_sha256: str) -> ReleaseManifest:
    if not _SHA256.fullmatch(expected_sha256):
        raise ValueError("Release manifest digest must be explicit SHA-256")
    with Path(path).open("rb") as source:
        raw = source.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Release manifest size or digest mismatch")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("schema") != "rag-release/v1":
        raise ValueError("Invalid release schema")
    epoch = data.get("epoch", "")
    if not isinstance(epoch, str) or not _EPOCH.fullmatch(epoch) or epoch == "default":
        raise ValueError("Release requires explicit non-default epoch")
    start, end = _day(data.get("valid_from")), _day(data.get("valid_until"))
    if start > end or not _string(data.get("approval_ref")):
        raise ValueError("Invalid release approval or applicability window")
    rows = data.get("records")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 500_000:
        raise ValueError("Release requires a bounded nonempty reviewed population")
    records = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid reviewed record")
        if not all(_string(row.get(key)) for key in ("record_id", "doc_id", "source_ref", "review_ref")):
            raise ValueError("Missing record identity or review reference")
        if row["record_id"] in records:
            raise ValueError("Duplicate reviewed record identity")
        content_hash = row.get("content_sha256")
        if not isinstance(content_hash, str) or not _SHA256.fullmatch(content_hash):
            raise ValueError("Invalid reviewed content digest")
        if not _mask(row.get("role_mask")) or not _mask(row.get("dept_mask")):
            raise ValueError("Invalid reviewed permission masks")
        valid_from, valid_until = _day(row.get("valid_from")), _day(row.get("valid_until"))
        if valid_from > valid_until:
            raise ValueError("Invalid record applicability window")
        records[row["record_id"]] = ApprovedRecord(
            row["doc_id"],
            content_hash,
            row["role_mask"],
            row["dept_mask"],
            valid_from,
            valid_until,
            row["source_ref"],
            row["review_ref"],
        )
    return ReleaseManifest(epoch, expected_sha256, start, end, MappingProxyType(records))


def current_manifest(epoch: str) -> ReleaseManifest:
    try:
        manifest = load_manifest(os.environ["RAG_RELEASE_MANIFEST"], os.environ["RAG_RELEASE_MANIFEST_SHA256"])
        if manifest.epoch != epoch or not manifest.active(china_today()):
            raise ValueError("Release epoch or validity window mismatch")
        return manifest
    except (KeyError, OSError, ValueError, TypeError) as exc:
        from core.exceptions import InfrastructureError

        raise InfrastructureError("Approved knowledge release unavailable", service="knowledge_release") from exc


def filter_release_candidates(candidates: list, epoch: str) -> list:
    if not industrial_enabled():
        return candidates
    manifest = current_manifest(epoch)
    today = china_today()
    return [candidate for candidate in candidates if manifest.accepts(candidate, today)]


def release_scope(epoch: str) -> str:
    if not industrial_enabled():
        return epoch
    return f"{epoch}:{current_manifest(epoch).digest}:{china_today().isoformat()}"


def validate_industrial_startup(config: dict) -> None:
    """Refuse unsafe industrial deployments before accepting traffic."""
    if not industrial_enabled():
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    if config.get("deployment_mode") != "production" or config.get("auth", {}).get("dev_mode"):
        raise RuntimeError("industrial profile requires production mode and verified identity")
    if os.environ.get("JWT_ALGORITHM") != "RS256":
        raise RuntimeError("industrial profile requires RS256")
    origins = os.environ.get("CORS_ORIGINS", "").split(",")
    if not all(origin.strip().startswith("https://") and "*" not in origin for origin in origins):
        raise RuntimeError("industrial profile requires explicit HTTPS origins")
    required = (
        "JWT_PRIVATE_KEY_PATH", "JWT_PUBLIC_KEY_PATH", "DATABASE_URL", "REDIS_PASSWORD",
        "QDRANT_API_KEY", "ELASTICSEARCH_USERNAME", "ELASTICSEARCH_PASSWORD",
        "VLLM_4B_URL", "VLLM_GEN_14B_URL",
    )
    if any(not os.environ.get(k, "").strip() for k in required):
        raise RuntimeError("industrial profile is missing provisioned credentials or endpoints")
    private = serialization.load_pem_private_key(Path(os.environ["JWT_PRIVATE_KEY_PATH"]).read_bytes(), password=None)
    public = serialization.load_pem_public_key(Path(os.environ["JWT_PUBLIC_KEY_PATH"]).read_bytes())
    if not isinstance(private, rsa.RSAPrivateKey) or not isinstance(public, rsa.RSAPublicKey):
        raise RuntimeError("industrial profile requires an RSA key pair")
    if private.key_size < 2048 or private.public_key().public_numbers() != public.public_numbers():
        raise RuntimeError("industrial JWT keys are weak or mismatched")
    db_url = os.environ["DATABASE_URL"]
    if not db_url.startswith("sqlite:////") or not Path(db_url.removeprefix("sqlite:///")).is_file():
        raise RuntimeError("industrial single-host identity database must be provisioned")
    if not config.get("elasticsearch", {}).get("host", "").startswith("https://"):
        raise RuntimeError("industrial Elasticsearch requires HTTPS")
    current_manifest(config.get("knowledge_version_epoch", ""))
