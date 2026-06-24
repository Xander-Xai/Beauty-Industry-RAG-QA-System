"""
Exception hierarchy for all RAG microservices.

Every service maps its internal exceptions to an appropriate HTTP status code
via these types.  The API gateway translates them into JSON error responses.
"""

from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    """
    Base exception for all service-level errors.

    Attributes:
        status_code: HTTP status code to return to the caller.
        detail: Human-readable error detail.
        error_code: Machine-readable error code (e.g. ``"REWRITE_FAILED"``).
        extra: Arbitrary extra payload for logging / debugging.
    """

    status_code: int = 500
    error_code: str = "SERVICE_ERROR"

    def __init__(
        self,
        message: str = "Internal service error",
        *,
        status_code: int | None = None,
        detail: str | None = None,
        error_code: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.detail = detail or message
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code
        self.extra = extra or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        result: dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
            "detail": self.detail,
            "status_code": self.status_code,
        }
        if self.extra:
            result["extra"] = self.extra
        return result


# ── 503 Service Unavailable ──────────────────────────────────────────────


class ServiceUnavailableError(ServiceError):
    """Raised when a downstream service is unreachable or unhealthy."""

    status_code = 503
    error_code = "SERVICE_UNAVAILABLE"

    def __init__(
        self,
        message: str = "Service unavailable",
        *,
        service_name: str = "",
        **kwargs: Any,
    ) -> None:
        extra = kwargs.pop("extra", {})
        if service_name:
            extra["service_name"] = service_name
        super().__init__(message, extra=extra, **kwargs)


# ── 429 Admission Rejected ──────────────────────────────────────────────


class AdmissionRejectedError(ServiceError):
    """Raised when the admission-control layer rejects a request."""

    status_code = 429
    error_code = "ADMISSION_REJECTED"

    def __init__(
        self,
        message: str = "Request rejected by admission control",
        *,
        retry_after: float | None = None,
        **kwargs: Any,
    ) -> None:
        extra = kwargs.pop("extra", {})
        if retry_after is not None:
            extra["retry_after_s"] = retry_after
        super().__init__(message, extra=extra, **kwargs)


# ── Rewrite ──────────────────────────────────────────────────────────────


class RewriteFailedError(ServiceError):
    """Raised when query rewriting fails."""

    status_code = 500
    error_code = "REWRITE_FAILED"

    def __init__(self, message: str = "Query rewrite failed", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


# ── Retrieval ────────────────────────────────────────────────────────────


class RetrievalError(ServiceError):
    """Raised when any retrieval path fails (dense, BM25, CLIP, ...)."""

    status_code = 500
    error_code = "RETRIEVAL_ERROR"

    def __init__(
        self,
        message: str = "Retrieval failed",
        *,
        path: str = "",
        **kwargs: Any,
    ) -> None:
        extra = kwargs.pop("extra", {})
        if path:
            extra["retrieval_path"] = path
        super().__init__(message, extra=extra, **kwargs)


# ── Generation ───────────────────────────────────────────────────────────


class GenerationError(ServiceError):
    """Raised when LLM generation fails or returns unexpected output."""

    status_code = 500
    error_code = "GENERATION_ERROR"

    def __init__(self, message: str = "Generation failed", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


# ── Cache ────────────────────────────────────────────────────────────────


class CacheError(ServiceError):
    """Raised when a cache operation (L1/L2) fails."""

    status_code = 500
    error_code = "CACHE_ERROR"

    def __init__(
        self,
        message: str = "Cache operation failed",
        *,
        cache_level: str = "",
        **kwargs: Any,
    ) -> None:
        extra = kwargs.pop("extra", {})
        if cache_level:
            extra["cache_level"] = cache_level
        super().__init__(message, extra=extra, **kwargs)


# ── 401 Authentication ──────────────────────────────────────────────────


class AuthenticationError(ServiceError):
    """Raised when the caller cannot be authenticated."""

    status_code = 401
    error_code = "AUTHENTICATION_ERROR"

    def __init__(self, message: str = "Authentication required", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


# ── 403 Authorization ───────────────────────────────────────────────────


class AuthorizationError(ServiceError):
    """Raised when the authenticated user lacks the required permissions."""

    status_code = 403
    error_code = "AUTHORIZATION_ERROR"

    def __init__(
        self,
        message: str = "Insufficient permissions",
        *,
        required_role: str = "",
        required_dept: str = "",
        **kwargs: Any,
    ) -> None:
        extra = kwargs.pop("extra", {})
        if required_role:
            extra["required_role"] = required_role
        if required_dept:
            extra["required_dept"] = required_dept
        super().__init__(message, extra=extra, **kwargs)


# ── 422 Validation ──────────────────────────────────────────────────────


class ValidationError(ServiceError):
    """Raised when request payload fails validation."""

    status_code = 422
    error_code = "VALIDATION_ERROR"

    def __init__(
        self,
        message: str = "Validation failed",
        *,
        fields: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        extra = kwargs.pop("extra", {})
        if fields:
            extra["fields"] = fields
        super().__init__(message, extra=extra, **kwargs)
