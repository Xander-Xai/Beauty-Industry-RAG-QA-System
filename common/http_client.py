"""
Async HTTP client for service-to-service communication.

Features:
- Automatic retries with exponential back-off
- Configurable per-request timeouts
- JSON encode / decode helpers
- Lightweight service registry (name -> base URL mapping)

Usage:
    registry = ServiceRegistry()
    async with ServiceClient(registry) as client:
        resp = await client.post_json("rewrite-service", "/rewrite", body={})
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from common.service_auth import get_service_headers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default service URL mapping (overridable via env or constructor)
# ---------------------------------------------------------------------------

_DEFAULT_REGISTRY: dict[str, str] = {
    "api-gateway": "http://api-gateway:8080",
    "rewrite-service": "http://rewrite-service:8081",
    "retrieval-service": "http://retrieval-service:8082",
    "generation-service": "http://generation-service:8083",
    "cache-service": "http://cache-service:8084",
    "monitoring-service": "http://monitoring-service:8085",
}


class ServiceRegistry:
    """
    Maps service names to their base URLs.

    Resolution order for each service:
    1. ``<SERVICE_NAME>_URL`` environment variable (uppercased)
    2. Explicit overrides passed to constructor
    3. Hard-coded defaults above
    """

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._urls: dict[str, str] = {}

        # 1. Defaults
        for name, url in _DEFAULT_REGISTRY.items():
            env_key = f"{name.upper().replace('-', '_')}_URL"
            self._urls[name] = os.environ.get(env_key, url)

        # 2. Caller overrides win over everything
        if overrides:
            self._urls.update(overrides)

        logger.info("ServiceRegistry initialised with %d services", len(self._urls))

    def get_url(self, service_name: str) -> str:
        """Return the base URL for *service_name*."""
        url = self._urls.get(service_name)
        if url is None:
            raise ValueError(
                f"Unknown service '{service_name}'. "
                f"Known: {list(self._urls.keys())}"
            )
        return url.rstrip("/")

    def register(self, service_name: str, base_url: str) -> None:
        """Register or overwrite a service URL at runtime."""
        self._urls[service_name] = base_url.rstrip("/")

    @property
    def services(self) -> dict[str, str]:
        """Return a copy of all registered URLs."""
        return dict(self._urls)


class ServiceClient:
    """
    Async HTTP client with built-in retry and timeout.

    Can be used as an async context manager::

        async with ServiceClient(registry) as client:
            result = await client.post_json("cache-service", "/get", body={"key": "k"})
    """

    def __init__(
        self,
        registry: ServiceRegistry | None = None,
        *,
        default_timeout: float = 30.0,
        max_retries: int = 3,
        retry_base_delay: float = 0.5,
        retry_max_delay: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._registry = registry or ServiceRegistry()
        self._default_timeout = default_timeout
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._extra_headers = headers or {}
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle ----------------------------------------------------------

    async def __aenter__(self) -> ServiceClient:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._default_timeout),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # Lazy init -- caller forgot the context manager; still works.
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._default_timeout),
                limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
            )
        return self._client

    # -- core request -------------------------------------------------------

    async def _request(
        self,
        method: str,
        service_name: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """
        Send an HTTP request with automatic retry on transient failures.

        Retried on: network errors, 429 (Too Many Requests), 502, 503, 504.
        """
        url = f"{self._registry.get_url(service_name)}{path}"
        client = self._ensure_client()

        merged_headers = {**self._extra_headers, **get_service_headers()}
        if headers:
            merged_headers.update(headers)

        request_timeout = timeout or self._default_timeout
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                response = await client.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                    headers=merged_headers,
                    timeout=request_timeout,
                )
                # Retry on transient HTTP status codes
                if response.status_code in (429, 502, 503, 504):
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    logger.warning(
                        "[%s] %s %s -> %d (attempt %d/%d), retrying in %.1fs",
                        service_name, method, path,
                        response.status_code, attempt, self._max_retries,
                        retry_after,
                    )
                    await asyncio.sleep(min(retry_after, self._retry_max_delay))
                    continue

                return response

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                last_exc = exc
                delay = min(
                    self._retry_base_delay * (2 ** (attempt - 1)),
                    self._retry_max_delay,
                )
                logger.warning(
                    "[%s] %s %s -> %s (attempt %d/%d), retrying in %.1fs",
                    service_name, method, path,
                    exc.__class__.__name__, attempt, self._max_retries,
                    delay,
                )
                await asyncio.sleep(delay)

        # Exhausted retries
        if last_exc:
            raise last_exc
        return response  # type: ignore[misc]  # last response was a 4xx/5xx

    # -- convenience methods ------------------------------------------------

    async def post_json(
        self,
        service_name: str,
        path: str,
        body: Any = None,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """POST JSON and return the raw httpx.Response."""
        return await self._request(
            "POST",
            service_name,
            path,
            json_body=body,
            params=params,
            headers=headers,
            timeout=timeout,
        )

    async def get_json(
        self,
        service_name: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """GET with optional query params and return the raw httpx.Response."""
        return await self._request(
            "GET",
            service_name,
            path,
            params=params,
            headers=headers,
            timeout=timeout,
        )

    async def post_json_data(
        self,
        service_name: str,
        path: str,
        body: Any = None,
        **kwargs: Any,
    ) -> Any:
        """POST JSON and return the decoded JSON body (convenience shorthand)."""
        resp = await self.post_json(service_name, path, body=body, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def get_json_data(
        self,
        service_name: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        """GET and return the decoded JSON body (convenience shorthand)."""
        resp = await self.get_json(service_name, path, **kwargs)
        resp.raise_for_status()
        return resp.json()
