"""Minimal FastAPI app exposing the real client-IP resolver for validation.

This is a validation harness, not a product entrypoint. It imports the real
``api.routes_auth._get_client_ip`` so the behaviour under a real reverse proxy
is exercised at the HTTP layer.
"""

from __future__ import annotations

from fastapi import FastAPI, Request

from api.routes_auth import _get_client_ip

app = FastAPI(title="trusted-proxy validation harness")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/whoami")
def whoami(request: Request) -> dict:
    return {
        "client_ip": _get_client_ip(request),
        "peer": request.client.host if request.client else None,
        "xff": request.headers.get("X-Forwarded-For", ""),
    }
