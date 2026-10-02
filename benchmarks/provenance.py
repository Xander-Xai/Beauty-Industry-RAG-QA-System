"""Run provenance and environment capture.

Two rules govern this module:

1. Nothing is guessed. A value that cannot be read from the live process or the
   repository is written as ``None`` and rendered as ``"unavailable"``.
2. Provenance never records secret material. Credential environment variables are
   deliberately *not* read; only their variable names are reported.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

UNAVAILABLE = "unavailable"

# Names only — the harness never reads secret values, and never writes them.
CREDENTIAL_ENV_NAMES = (
    "OPENAI_API_KEY",
    "ELASTICSEARCH_PASSWORD",
    "REDIS_PASSWORD",
    "SERVICE_AUTH_TOKEN",
    "MINIO_SECRET_KEY",
)

SECRET_ENV_NAMES = frozenset(CREDENTIAL_ENV_NAMES)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def _git(args: list[str], cwd: str | Path) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed git argv, no shell
            ["git", *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # Empty stdout is a meaningful result (for example a clean
    # `git status --porcelain`), so it must not collapse into None.
    return completed.stdout.strip()


def git_provenance(cwd: str | Path = ".") -> tuple[str | None, bool | None]:
    """Return ``(git_sha, git_dirty)``.

    ``git_dirty`` is ``None`` only when git could not answer at all; a clean tree
    is ``False``, which is what makes a clean artifact attest to the repository
    state.
    """
    sha = _git(["rev-parse", "HEAD"], cwd)
    if sha is None:
        return None, None
    status = _git(["status", "--porcelain"], cwd)
    dirty = None if status is None else bool(status)
    return sha, dirty


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_json(payload: Any) -> str:
    import json

    return sha256_bytes(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))


def _module_version(module_name: str) -> str | None:
    try:
        module = __import__(module_name)
    except Exception:
        return None
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as pkg_version

        return pkg_version(module_name)
    except (PackageNotFoundError, Exception):
        return None


def _torch_info() -> dict[str, Any]:
    try:
        import torch
    except Exception:
        return {
            "torch_version": None,
            "cuda_available": None,
            "cuda_version": None,
            "gpu_name": None,
            "gpu_vram": None,
        }
    cuda_available: bool | None = bool(torch.cuda.is_available()) if hasattr(torch, "cuda") else None
    gpu_name: str | None = None
    gpu_vram: int | None = None
    if cuda_available and hasattr(torch, "cuda"):
        try:
            gpu_name = torch.cuda.get_device_name(0)
            total = torch.cuda.get_device_properties(0)
            gpu_vram = int(total.total_memory)
        except Exception:
            gpu_name, gpu_vram = None, None
    return {
        "torch_version": str(torch.__version__),
        "cuda_available": cuda_available,
        "cuda_version": getattr(torch.version, "cuda", None),
        "gpu_name": gpu_name,
        "gpu_vram": gpu_vram,
    }


def _ram_bytes() -> int | None:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None
    return int(page_size * page_count)


def credential_env_state() -> dict[str, str]:
    """Report only *whether* a credential variable is set — never its value."""
    state: dict[str, str] = {}
    for name in CREDENTIAL_ENV_NAMES:
        state[name] = "set" if os.environ.get(name) else "unset"
    return state


def collect_environment() -> dict[str, Any]:
    """Capture the environment facts that affect reproducibility."""
    environment: dict[str, Any] = {
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu": platform.processor() or None,
        "ram_bytes": _ram_bytes(),
        "qdrant_version": _module_version("qdrant_client"),
        "elasticsearch_version": _module_version("elasticsearch"),
        "embedding_model": None,
        "embedding_model_revision": None,
        "biencoder_model": None,
        "biencoder_model_revision": None,
        "crossencoder_model": None,
        "crossencoder_model_revision": None,
        "credential_env_state": credential_env_state(),
    }
    environment.update(_torch_info())
    return environment


def render_environment(environment: dict[str, Any]) -> dict[str, Any]:
    """Render environment values with ``unavailable`` placeholders."""
    rendered: dict[str, Any] = {}
    for key, value in environment.items():
        if isinstance(value, dict):
            rendered[key] = {k: (UNAVAILABLE if v is None else v) for k, v in value.items()}
        elif value is None:
            rendered[key] = UNAVAILABLE
        else:
            rendered[key] = value
    return rendered


_SECRET_KEY_MARKERS = ("password", "secret", "token", "api_key", "credential", "access_key", "private_key")


def redact_url_userinfo(value: Any) -> Any:
    """Strip ``user:password@`` credentials from a URL string.

    The sanitizer matches on *keys*, so a credential embedded in a URL under a
    harmless key such as ``host`` would survive untouched. Any URL-shaped string
    that is persisted is redacted here instead.
    """
    if not isinstance(value, str) or "@" not in value:
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.netloc or "@" not in parts.netloc:
        return value
    host = parts.netloc.rsplit("@", 1)[-1]
    redacted = urlunsplit((parts.scheme, f"***:***@{host}", parts.path, parts.query, parts.fragment))
    return redacted


def _elastic_username() -> str | None:
    return os.environ.get("ELASTICSEARCH_USERNAME") or _config_elastic_field("username")


def _elastic_password() -> str | None:
    return os.environ.get("ELASTICSEARCH_PASSWORD") or _config_elastic_field("password")


def _config_elastic_field(field: str) -> str | None:
    from common.config import get_config_dict

    value = (get_config_dict().get("elasticsearch", {}) or {}).get(field)
    return str(value) if value else None


def _elastic_principal_fingerprint() -> str | None:
    """Non-reversible identifier for the Elasticsearch credential in effect."""
    username = _elastic_username()
    password = _elastic_password()
    if not username and not password:
        return None
    digest = hashlib.sha256(f"{username or ''}:{password or ''}".encode()).hexdigest()
    return f"sha256:{digest[:16]}"


def _is_secret_key(key: Any) -> bool:
    lowered = str(key).lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def sanitize(value: Any) -> Any:
    """Recursively replace credential-ish values with presence flags.

    Applied to everything persisted from the live configuration: artifacts get
    shared and promoted, so a secret nested anywhere in the config tree must not
    be written verbatim.
    """
    if isinstance(value, dict):
        return {key: (bool(item) if _is_secret_key(key) else sanitize(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    return redact_url_userinfo(value)


def _sanitize_models(models: Any) -> dict[str, Any]:
    """Model settings with any credential-ish value reduced to a presence flag."""
    if not isinstance(models, dict):
        return {}
    return {name: sanitize(settings) for name, settings in models.items()}


def effective_retrieval_config() -> dict[str, Any]:
    """A sanitized snapshot of the retrieval settings that actually apply.

    Hashing only the requested configuration names is not enough to reproduce a
    run: a different ``CONFIG_PATH`` or Qdrant/Elasticsearch environment
    override can query a different index, collection or model revision while
    producing the same configuration hash. The snapshot therefore includes the
    env-overridden config values plus the environment variables the retrieval
    layer reads. Secret values are reduced to presence flags.
    """
    from common.config import get_config_dict

    config = get_config_dict() or {}
    retrieval = config.get("retrieval", {}) or {}
    elastic = config.get("elasticsearch", {}) or {}
    qdrant = config.get("qdrant", {}) or {}
    embedding = config.get("embedding", {}) or {}
    snapshot = {
        "config_path": os.environ.get("CONFIG_PATH") or "config.json",
        "knowledge_version_epoch": config.get("knowledge_version_epoch"),
        "retrieval": retrieval,
        "rrf": retrieval.get("rrf"),
        "parallel_paths": retrieval.get("parallel_paths"),
        "rerank": retrieval.get("rerank"),
        "bi_encoder": retrieval.get("bi_encoder"),
        # The cross-encoder ensemble reads gpu1.models.cross_encoder_{a,b}; without
        # these a change of reranker path/revision would leave config_sha256
        # identical for hybrid_rrf_biencoder_crossencoder.
        "cross_encoder": _sanitize_models((config.get("gpu1", {}) or {}).get("models", {})),
        "elasticsearch": {
            "host": elastic.get("host"),
            "index": elastic.get("index"),
            "enabled": elastic.get("enabled"),
            # Presence flags alone collapse every principal into the same
            # snapshot, so two credentials that see different corpora (for example
            # under document-level security) would produce an identical
            # config_sha256. A salted fingerprint identifies the principal without
            # being reversible back to the credential.
            "username_set": bool(_elastic_username()),
            "password_set": bool(_elastic_password()),
            "principal_fingerprint": _elastic_principal_fingerprint(),
        },
        "qdrant": {
            "host": qdrant.get("host"),
            "port": qdrant.get("port"),
            "grpc_port": qdrant.get("grpc_port"),
            "collections": qdrant.get("collections"),
        },
        "embedding": embedding,
        "env_overrides": {
            name: os.environ.get(name)
            for name in (
                "QDRANT_HOST",
                "QDRANT_PORT",
                "QDRANT_GRPC_PORT",
                "ELASTICSEARCH_USERNAME",
                "TRUSTED_PROXIES",
            )
            if os.environ.get(name)
        },
        "secret_env_presence": credential_env_state(),
    }
    # Applied once at the boundary so a credential nested anywhere in the
    # configuration tree is reduced to a presence flag before it is persisted.
    return sanitize(snapshot)


def write_gitignore_entry(repo_root: Path, entry: str) -> bool:
    """Append ``entry`` to ``.gitignore`` when absent. Returns True if written."""
    gitignore = repo_root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if entry in existing.splitlines():
        return False
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    gitignore.write_text(f"{existing}{prefix}{entry}\n", encoding="utf-8")
    return True
