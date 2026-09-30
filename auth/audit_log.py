"""Audit logging middleware for tracking user queries and access control."""

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("audit")


@dataclass
class AuditEntry:
    timestamp: float
    user_id: str
    role_mask: int
    dept_mask: int
    method: str
    path: str
    query_hash: str  # SHA256 of user_query
    status_code: int
    latency_ms: float
    ip_address: str
    filter_expression: str = ""
    intercept_reason: str = ""


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Middleware that logs all API requests with security-relevant details."""

    # Paths to skip audit logging
    SKIP_PATHS = {"/api/health", "/docs", "/openapi.json", "/redoc"}

    # Sensitive query patterns that trigger full redaction
    SENSITIVE_PATTERNS = ["配方", "formula", "secret", "专利", "patent"]

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        start_time = time.time()

        # Extract identity from headers (pre-auth)
        user_id = request.headers.get("X-User-ID", "anonymous")
        try:
            role_mask = int(request.headers.get("X-Role-Mask", "0"))
            dept_mask = int(request.headers.get("X-Dept-Mask", "0"))
        except ValueError:
            role_mask = 0
            dept_mask = 0

        # Read query for audit (body)
        query_hash = ""
        body = None
        if request.method in ("POST", "PUT"):
            try:
                body = await request.body()
                if body:
                    data = json.loads(body)
                    query = data.get("query", "")
                    if query:
                        # Hash the query for privacy
                        query_hash = hashlib.sha256(query.encode()).hexdigest()[:16]
                        # Check for sensitive patterns
                        for pattern in self.SENSITIVE_PATTERNS:
                            if pattern in query.lower():
                                query_hash = "[REDACTED]"
                                break
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        # Process request
        response = await call_next(request)

        # Calculate latency
        latency_ms = (time.time() - start_time) * 1000

        # Get client IP
        client_ip = request.client.host if request.client else "unknown"

        # Create audit entry
        entry = AuditEntry(
            timestamp=start_time,
            user_id=user_id,
            role_mask=role_mask,
            dept_mask=dept_mask,
            method=request.method,
            path=request.url.path,
            query_hash=query_hash,
            status_code=response.status_code,
            latency_ms=round(latency_ms, 2),
            ip_address=client_ip,
        )

        # Log based on severity
        if response.status_code >= 500:
            logger.error(json.dumps(asdict(entry), ensure_ascii=False))
        elif response.status_code == 403:
            logger.warning(json.dumps(asdict(entry), ensure_ascii=False))
        else:
            logger.info(json.dumps(asdict(entry), ensure_ascii=False))

        return response
