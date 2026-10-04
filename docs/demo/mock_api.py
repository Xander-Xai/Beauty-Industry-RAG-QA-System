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

This server is a demo fixture. It is not part of the request path and must
never be started in a deployment.
"""

from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CORPUS_PATH = Path(__file__).with_name("synthetic_corpus.json")

logger = logging.getLogger("demo.mock_api")


def load_corpus() -> dict:
    with CORPUS_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


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
        role_mask = self.headers.get("X-Role-Mask")

        if path in ("/api/query", "/api/chat"):
            user_text = payload.get("query") or payload.get("message") or ""
            if not user_text.strip():
                self._send_json({"error": "validation_error", "detail": "empty query"}, status=400)
                return
            # Dev-mode identities arrive as bitmask headers. The synthetic
            # corpus grants the regulation role access to both documents, so a
            # different role ends up with an empty post-filter evidence set —
            # the refusal branch, not a second fabricated answer.
            authorized = role_mask == str(self.corpus["_privileged_role_mask"])
            self._send_json(
                {
                    "answer": (
                        self.corpus["answer_markdown"] if authorized else self.corpus["answer_refusal_markdown"]
                    ),
                    "session_id": query["session_id"],
                    "business_type": query["business_type"],
                    "intent": query["intent"],
                    "evidence_doc_ids": self.corpus["evidence_doc_ids"] if authorized else [],
                    "latency_ms": query["latency_ms"] if authorized else query["refusal_latency_ms"],
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
