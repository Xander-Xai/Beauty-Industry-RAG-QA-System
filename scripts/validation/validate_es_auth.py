#!/usr/bin/env python3
"""Real authenticated Elasticsearch validation (online + offline paths).

Requires a running Elasticsearch with xpack.security.enabled=true.

Env:
  VALIDATION_ES_HOST       (default http://127.0.0.1:9200)
  ELASTICSEARCH_USERNAME   (default elastic)
  ELASTICSEARCH_PASSWORD   (required; no production secret)

Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

ES_HOST = os.environ.get("VALIDATION_ES_HOST", "http://127.0.0.1:9200")
ES_USER = os.environ.get("ELASTICSEARCH_USERNAME", "elastic")
ES_PASSWORD = os.environ.get("ELASTICSEARCH_PASSWORD", "validation-es-password")
TEST_INDEX = "cosmetics_docs"


def _log(ok: bool, message: str) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {message}")


def _make_client(password: str):
    from elasticsearch import Elasticsearch

    return Elasticsearch(ES_HOST, basic_auth=(ES_USER, password), request_timeout=15)


def main() -> int:
    from elasticsearch import AuthenticationException

    failures = 0

    # 1. Anonymous / wrong-credential requests must fail.
    try:
        _make_client("wrong-password").info()
        _log(False, "wrong password was accepted")
        failures += 1
    except AuthenticationException:
        _log(True, "wrong password rejected")
    except Exception as exc:  # noqa: BLE001 - surface unexpected error
        _log(False, f"wrong password raised unexpected {type(exc).__name__}: {exc}")
        failures += 1

    # 2. Correct credentials succeed.
    client = _make_client(ES_PASSWORD)
    info = client.info()
    version = info["version"]["number"]
    _log(True, f"authenticated client connected (Elasticsearch {version})")

    # 3. Offline writer path: explicit mapping, upsert, search_after pagination.
    from offline.elasticsearch_writer import ElasticsearchWriter

    writer = ElasticsearchWriter(client, index_name=TEST_INDEX, page_size=2)
    writer.ensure_index(recreate=True, confirm=True)
    _log(True, "ElasticsearchWriter.ensure_index(recreate) + mapping validated")

    epoch = "validation_epoch"
    docs = [
        writer.build_document(
            types.SimpleNamespace(
                doc_id="doc-1",
                chunk_id=f"chunk-{i}",
                chunk_index=i,
                content=f"烟酰胺 安全浓度 验证文档 {i}",
                source_path="/data/doc.txt",
                role_mask=0,
                dept_mask=0,
                status="active",
                doc_version_epoch=epoch,
                embedding_type="bge",
                embedding_version="validation-v1",
                metadata={"page": i},
            )
        )
        for i in range(3)
    ]
    writer.upsert_documents(docs)
    client.indices.refresh(index=TEST_INDEX)
    found = writer.documents_for_epoch(epoch)
    _log(len(found) == 3, f"search_after pagination returned {len(found)}/3 docs (page_size=2)")
    failures += 0 if len(found) == 3 else 1
    count = writer.count_documents(epoch)
    _log(count == 3, f"count_documents == {count}")
    failures += 0 if count == 3 else 1

    # 4. Online retriever path against the authenticated index.
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    config.setdefault("elasticsearch", {})
    config["elasticsearch"]["host"] = ES_HOST
    config["elasticsearch"]["index"] = TEST_INDEX
    config["elasticsearch"]["enabled"] = True
    # Align the active epoch with the documents written above so the BM25
    # version filter can match them.
    config["knowledge_version_epoch"] = epoch
    tmp_config = Path(tempfile.mkdtemp(prefix="rag-es-")) / "config.json"
    tmp_config.write_text(json.dumps(config), encoding="utf-8")

    os.environ["CONFIG_PATH"] = str(tmp_config)
    os.environ["ELASTICSEARCH_USERNAME"] = ES_USER
    os.environ["ELASTICSEARCH_PASSWORD"] = ES_PASSWORD

    from retrieval.bm25_retriever import BM25Retriever

    retriever = BM25Retriever()
    hits = retriever.search("烟酰胺 安全浓度", user_role_mask=0, user_dept_mask=0, top_k=5)
    _log(bool(hits), f"BM25Retriever.search returned {len(hits)} hit(s) over authenticated ES")
    failures += 0 if hits else 1

    # 5. Retriever auth failure degrades safely instead of crashing.
    os.environ["ELASTICSEARCH_PASSWORD"] = "definitely-wrong"  # noqa: S105 - intentional invalid credential
    bad = BM25Retriever()
    assert bad.search("烟酰胺", user_role_mask=0, user_dept_mask=0, top_k=5) == []
    _log(True, "BM25Retriever with bad credentials degrades to empty result (no crash)")
    os.environ["ELASTICSEARCH_PASSWORD"] = ES_PASSWORD

    # Cleanup the validation index.
    client.indices.delete(index=TEST_INDEX, ignore_unavailable=True)

    print()
    if failures:
        print(f"ES AUTH VALIDATION FAILED ({failures})")
        return 1
    print("ES AUTH VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
