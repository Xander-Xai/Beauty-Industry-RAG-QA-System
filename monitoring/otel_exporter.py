"""Opt-in OTLP span export for the online tracing hook.

The tracing hook in :mod:`monitoring.otel_tracer` already exists on the online
pipeline path. What it lacked was a deployable way to get spans *out* of the
process. This module adds that, under three constraints that matter more than
the feature itself:

1. **Off by default.** ``OTEL_EXPORT_ENABLED=false``. With it false, no exporter
   is constructed and behaviour is exactly as before.
2. **Never fatal.** A missing exporter package, a bad endpoint or a collector
   that refuses connections must not turn into a failed ``/api/query``. Every
   failure degrades to a warning plus a metric, and the service keeps serving.
3. **No secrets in spans.** Attribute values are allow-listed and scrubbed, so a
   raw query, token or document excerpt cannot reach a trace backend.

Configuration (environment, so no credential is ever committed):

``OTEL_EXPORT_ENABLED``
    ``true`` to enable. Default ``false``.
``OTEL_EXPORTER_OTLP_ENDPOINT``
    Base endpoint, e.g. ``http://otel-collector:4318``.
``OTEL_EXPORTER_OTLP_PROTOCOL``
    ``http/protobuf`` (default) or ``grpc``.
``OTEL_EXPORTER_OTLP_HEADERS``
    ``key=value,key2=value2``. Treated as a secret: never logged, never put in a
    span attribute.
``OTEL_SERVICE_NAME``
    Reported as ``service.name``. Defaults to ``rag-api``.

The exporter package itself is optional. Install
``-r requirements-otel.txt`` to enable export; without it the module reports
``exporter_unavailable`` and the application is unaffected.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Metric exposed on /api/metrics so an operator can see whether export is on
#: without reading logs. 0 = disabled, 1 = enabled and initialised.
EXPORTER_ENABLED_METRIC = "otel.exporter_enabled"

DEFAULT_SERVICE_NAME = "rag-api"

#: Attribute names that may carry a span. Anything not listed here is dropped,
#: which is what keeps query text, tokens and document content out of traces.
ALLOWED_ATTRIBUTE_KEYS = frozenset(
    {
        "request_id",
        "route",
        "method",
        "status_code",
        "business_type",
        "intent",
        "decision",
        "retrieval_path_count",
        "document_count",
        "evidence_count",
        "error_type",
        "duration_ms",
        "cache_hit_level",
        "route_tier",
        "degraded",
    }
)

#: Values matching these shapes are replaced regardless of key. A credential can
#: reach a span as a *value* under an allow-listed name if a caller passes the
#: wrong thing, so shape is checked too.
_BEARER_RE = re.compile(r"^\s*bearer\s+\S", re.IGNORECASE)
_JWT_RE = re.compile(r"^ey[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*$")

REDACTED = "[REDACTED]"

#: Longest attribute value kept. A span carrying a whole document is both a
#: privacy problem and a backend-size problem.
MAX_VALUE_LENGTH = 256


class ExporterState:
    """Observable outcome of the exporter setup attempt."""

    DISABLED = "disabled"
    ENABLED = "enabled"
    UNAVAILABLE = "exporter_unavailable"
    FAILED = "initialization_failed"

    ALL = (DISABLED, ENABLED, UNAVAILABLE, FAILED)


@dataclass
class OtelExporterConfig:
    """Resolved export configuration."""

    enabled: bool = False
    endpoint: str = ""
    protocol: str = "http/protobuf"
    headers: dict[str, str] = field(default_factory=dict)
    service_name: str = DEFAULT_SERVICE_NAME

    @property
    def headers_present(self) -> bool:
        """Whether any header is configured.

        Presence only — header values are credentials and are never exposed.
        """
        return bool(self.headers)

    def describe(self) -> dict[str, Any]:
        """Secret-free description, safe to log and to place in a span."""
        return {
            "enabled": self.enabled,
            "endpoint": self.endpoint or None,
            "protocol": self.protocol,
            "service_name": self.service_name,
            "headers_present": self.headers_present,
            "header_names": sorted(self.headers),
        }


def _parse_headers(raw: str) -> dict[str, str]:
    """Parse ``k=v,k2=v2``. Malformed entries are dropped, not fatal."""
    headers: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, _, value = item.partition("=")
        key = key.strip()
        if key:
            headers[key] = value.strip()
    return headers


def load_config(environ: dict[str, str] | None = None) -> OtelExporterConfig:
    """Resolve configuration from the environment.

    ``enabled`` is read strictly: only an explicit ``true``/``1`` turns export
    on, so an unset or misspelled value leaves tracing exactly as it was.
    """
    env = environ if environ is not None else os.environ
    raw_enabled = (env.get("OTEL_EXPORT_ENABLED") or "").strip().lower()
    enabled = raw_enabled in {"true", "1", "yes", "on"}
    protocol = (env.get("OTEL_EXPORTER_OTLP_PROTOCOL") or "http/protobuf").strip()
    if protocol not in {"http/protobuf", "grpc"}:
        logger.warning(
            "Unsupported OTEL_EXPORTER_OTLP_PROTOCOL %r; falling back to http/protobuf",
            protocol,
        )
        protocol = "http/protobuf"
    return OtelExporterConfig(
        enabled=enabled,
        endpoint=(env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip().rstrip("/"),
        protocol=protocol,
        headers=_parse_headers(env.get("OTEL_EXPORTER_OTLP_HEADERS") or ""),
        service_name=(env.get("OTEL_SERVICE_NAME") or DEFAULT_SERVICE_NAME).strip() or DEFAULT_SERVICE_NAME,
    )


def sanitize_attributes(attributes: dict[str, Any] | None) -> dict[str, Any]:
    """Reduce span attributes to the allow-list and scrub the rest.

    Non-allow-listed keys are dropped entirely rather than redacted, so a caller
    cannot smuggle a payload through an unapproved name. Values that look like
    credentials are redacted, and long values are truncated.
    """
    if not attributes:
        return {}
    safe: dict[str, Any] = {}
    for key, value in attributes.items():
        if key not in ALLOWED_ATTRIBUTE_KEYS:
            continue
        if isinstance(value, str):
            if _BEARER_RE.match(value) or _JWT_RE.match(value):
                safe[key] = REDACTED
                continue
            safe[key] = value[:MAX_VALUE_LENGTH]
            continue
        if isinstance(value, (int, float, bool)):
            safe[key] = value
            continue
        # Structured values are summarised rather than serialised.
        if isinstance(value, (list, tuple, set, frozenset)):
            safe[key] = len(value)
            continue
        if isinstance(value, dict):
            safe[key] = len(value)
            continue
        safe[key] = str(value)[:MAX_VALUE_LENGTH]
    return safe


def build_span_processor(config: OtelExporterConfig) -> tuple[Any | None, str, str]:
    """Build a span processor. Returns ``(processor, state, reason)``.

    ``reason`` is always a human-readable string describing the outcome.

    Never raises: an unavailable exporter package or a broken endpoint yields
    ``(None, state, reason)`` so the caller can log it and continue.
    """
    if not config.enabled:
        return None, ExporterState.DISABLED, "OTEL_EXPORT_ENABLED is not set to true"
    if not config.endpoint:
        return None, ExporterState.FAILED, "OTEL_EXPORT_ENABLED=true but OTEL_EXPORTER_OTLP_ENDPOINT is empty"

    try:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        return None, ExporterState.UNAVAILABLE, f"opentelemetry sdk unavailable: {exc}"

    try:
        exporter = _build_exporter(config)
    except ImportError as exc:
        return None, ExporterState.UNAVAILABLE, f"OTLP exporter package not installed: {exc}"
    except Exception as exc:
        return None, ExporterState.FAILED, f"exporter construction failed: {type(exc).__name__}: {exc}"

    try:
        processor = BatchSpanProcessor(exporter)
    except Exception as exc:
        return None, ExporterState.FAILED, f"span processor construction failed: {type(exc).__name__}: {exc}"

    return processor, ExporterState.ENABLED, f"exporting via {config.protocol} to {config.endpoint}"


def _build_exporter(config: OtelExporterConfig) -> Any:
    if config.protocol == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(endpoint=config.endpoint, headers=config.headers or None)

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    # The http exporter appends /v1/traces to a base endpoint.
    return OTLPSpanExporter(endpoint=f"{config.endpoint}/v1/traces", headers=config.headers or None)


def build_resource(config: OtelExporterConfig) -> Any | None:
    """Resource carrying the service name, when the SDK is importable."""
    try:
        from opentelemetry.sdk.resources import Resource
    except ImportError:
        return None
    return Resource.create({"service.name": config.service_name})
