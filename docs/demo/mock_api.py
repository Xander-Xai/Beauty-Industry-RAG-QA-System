#!/usr/bin/env python3
"""Synthetic mock backend for README demo capture — stdlib only.

Purpose
-------
`docs/demo/capture_demo.py` renders the **real** frontend
(`frontend/src/App.jsx`) in Chromium and points its `/api` proxy at this
server, so the screenshot in the README is a real UI render rather than a
mock-up drawn by hand.

Data
----
Every payload comes from `synthetic_corpus.json`, which is fictional:
placeholder document ids, a placeholder CAS number, invented standard
names. No real regulation text, no proprietary corpus, no production logs,
no credentials, no traffic figures.

Fidelity
--------
Response shapes mirror the real contracts in `api/models.py`
(`QueryResponse`, `ChatResponse`, `StatsResponse`) and the real handlers in
`api/routes.py` / `api/routes_auth.py`. Where this server invents a *value*
it is synthetic; where it returns a *shape* it matches the repository.

Document authorization is decided per document by ``authorized_doc_ids``,
which mirrors ``common.auth.is_allowed`` (admin bypass, ``role_mask == 0``
means no role restriction, ``dept_mask == 0`` means no dept restriction,
otherwise both masks must overlap). That predicate is re-implemented here
rather than imported so this fixture stays stdlib-only and startable without
the service dependencies; ``tests/test_demo_corpus_rbac_consistency.py``
asserts the two agree, so the copy cannot drift from the real one.

This server is a demo fixture. It is not part of the request path and must
never be started in a deployment. Its authorization behaviour is **not**
runtime validation of RBAC — it proves only that the synthetic corpus and this
fixture are internally consistent with the repository's documented semantics.
"""

from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CORPUS_PATH = Path(__file__).with_name("synthetic_corpus.json")

# Mirrors ``config.json`` -> ``rbac.super_admin_mask``. The real
# ``/api/auth/metadata`` does not publish this value, so it cannot be read from
# the corpus; a repository test asserts it against the loaded config.
SUPER_ADMIN_MASK = 0xFFFFFFFF

logger = logging.getLogger("demo.mock_api")


def load_corpus() -> dict:
    with CORPUS_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_rbac() -> dict:
    """The RBAC block the demo serves at ``/api/auth/metadata``.

    Read per call instead of cached: the corpus is a few KiB and the capture
    issues a handful of requests, so a cache would only add mutable global
    state to a fixture.
    """
    return load_corpus()["auth_metadata"]["rbac"]


def is_allowed(
    doc_role_mask: int,
    user_role_mask: int,
    doc_dept_mask: int,
    user_dept_mask: int,
) -> bool:
    """Mirror of ``common.auth.is_allowed``; kept in sync by a repository test.

    Deliberately not imported: this fixture must stay stdlib-only and startable
    without the service dependencies. Role and department masks come from the
    corpus (the same values the real ``/api/auth/metadata`` serves), while
    ``SUPER_ADMIN_MASK`` mirrors ``config.json`` -> ``rbac.super_admin_mask``,
    which that endpoint does not expose. A repository test asserts both
    against the real config so neither copy can drift.
    """
    rbac = load_rbac()
    if user_role_mask in {SUPER_ADMIN_MASK, rbac["roles"].get("admin")}:
        return True
    if doc_role_mask == 0:
        if doc_dept_mask == 0:
            return True
        return (doc_dept_mask & user_dept_mask) != 0
    role_ok = (doc_role_mask & user_role_mask) != 0
    dept_ok = doc_dept_mask == 0 or (doc_dept_mask & user_dept_mask) != 0
    return role_ok and dept_ok


def authorized_doc_ids(corpus: dict, role_mask: int, dept_mask: int) -> list[str]:
    """Evidence ids the given identity may actually see, in corpus order.

    Filtering is per document rather than per role: an identity that clears
    one document but not another must get exactly the cleared subset, never
    the whole set and never an answer whose citations it cannot open.
    """
    return [
        doc["doc_id"]
        for doc in corpus["documents"]
        if doc["doc_id"] in corpus["evidence_doc_ids"]
        and is_allowed(doc["role_mask"], role_mask, doc["dept_mask"], dept_mask)
    ]


def _header_mask(headers, name: str) -> int:
    """Read one dev-mode bitmask header, failing closed to 0.

    ``common.auth.parse_identity`` coerces these headers with a bare
    ``int(...)``. This fixture is stricter on purpose: an absent, malformed,
    negative or over-uint32 value becomes mask 0, i.e. an anonymous identity
    that clears no document and lands on the refusal branch. A demo fixture has
    no reason to be more permissive than the service it stands in for, and the
    capture never exercises this path.
    """
    raw = headers.get(name)
    if raw is None:
        return 0
    try:
        mask = int(raw)
    except ValueError:
        return 0
    if not 0 <= mask <= 0xFFFFFFFF:
        return 0
    return mask


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "SyntheticDemoAPI/1.0"
    protocol_version = "HTTP/1.1"

    corpus: dict = {}

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002 - stdlib signature
        logger.info("%s %s", self.address_string(), fmt % args)

    # ── helpers ──────────────────────────────────────────────
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ── routes ───────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        path, _, query = self.path.partition("?")

        if path == "/api/auth/metadata":
            self._send_json(self.corpus["auth_metadata"])
            return

        if path == "/api/dialog_history":
            history = self.corpus["dialog_history"]
            self._send_json(
                {
                    "session_id": self.corpus["query"]["session_id"],
                    "rounds": history["rounds"],
                    "locked_doc_ids": history["locked_doc_ids"],
                }
            )
            return

        if path == "/api/stats":
            self._send_json(self.corpus["stats"])
            return

        if path == "/api/health":
            self._send_json(
                {
                    "status": "healthy",
                    "version": self.corpus["auth_metadata"]["app"]["version"],
                    "dependencies": {"redis": True, "qdrant": True, "elasticsearch": True},
                }
            )
            return

        if path.startswith("/api/media/"):
            doc_id = path.removeprefix("/api/media/")
            known = {doc["doc_id"] for doc in self.corpus["documents"]}
            if doc_id not in known:
                self._send_json(
                    {"error": "not_found", "detail": f"文档 {doc_id} 不存在"},
                    status=404,
                )
                return
            self._send_json(
                {
                    "doc_id": doc_id,
                    "url": f"synthetic://demo/{doc_id}",
                    "expires_in_seconds": 60,
                }
            )
            return

        self._send_json({"error": "not_found", "detail": path}, status=404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        path = self.path.partition("?")[0]
        payload = self._read_json()
        query = self.corpus["query"]

        if path in ("/api/query", "/api/chat"):
            user_text = payload.get("query") or payload.get("message") or ""
            if not user_text.strip():
                self._send_json({"error": "validation_error", "detail": "empty query"}, status=400)
                return
            # Dev-mode identities arrive as bitmask headers, exactly as
            # `common.auth.parse_identity` reads them. Evidence is filtered per
            # document with the same predicate the retrieval layer uses, so the
            # demo can only show a citation the current identity may actually
            # open — and an identity that clears nothing gets the refusal
            # branch instead of a second fabricated answer.
            role_mask = _header_mask(self.headers, "X-Role-Mask")
            dept_mask = _header_mask(self.headers, "X-Dept-Mask")
            evidence = authorized_doc_ids(self.corpus, role_mask, dept_mask)
            answered = bool(evidence)
            self._send_json(
                {
                    "answer": (self.corpus["answer_markdown"] if answered else self.corpus["answer_refusal_markdown"]),
                    "session_id": query["session_id"],
                    "business_type": query["business_type"],
                    "intent": query["intent"],
                    "evidence_doc_ids": evidence,
                    "latency_ms": query["latency_ms"] if answered else query["refusal_latency_ms"],
                    "cache_hit": False,
                }
            )
            return

        if path == "/api/continuation":
            self._send_json(
                {
                    "answer": "",
                    "has_more": False,
                    "session_id": payload.get("session_id"),
                    "outline": [],
                }
            )
            return

        self._send_json({"error": "not_found", "detail": path}, status=404)


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic demo API for README capture")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
    )

    DemoHandler.corpus = load_corpus()
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    print(f"synthetic demo API on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
