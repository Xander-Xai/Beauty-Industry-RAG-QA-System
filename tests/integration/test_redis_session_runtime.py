"""Real Redis + multi-process validation for SessionState persistence.

These tests cross a real process boundary against a real redis-server. They do
not use a fake Redis client and each subprocess initialises its own client, so
a passing run means cross-worker sharing actually works.

Gate: set RUN_RUNTIME_VALIDATION=1 and point at a disposable Redis via
VALIDATION_REDIS_HOST / VALIDATION_REDIS_PORT / VALIDATION_REDIS_PASSWORD.
They are skipped by default so normal CI does not require external services.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.runtime]

ROOT = Path(__file__).resolve().parents[2]

REDIS_HOST = os.environ.get("VALIDATION_REDIS_HOST", "127.0.0.1")
REDIS_PORT = os.environ.get("VALIDATION_REDIS_PORT", "6380")
REDIS_PASSWORD = os.environ.get("VALIDATION_REDIS_PASSWORD", "validation-password")


def _runtime_enabled() -> bool:
    return os.environ.get("RUN_RUNTIME_VALIDATION") == "1"


def _redis_env(port: str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "DEPLOYMENT_MODE": "development",
            "REDIS_PASSWORD": REDIS_PASSWORD,
            "REDIS_CACHE_HOST": REDIS_HOST,
            "REDIS_CACHE_PORT": port or REDIS_PORT,
            "REDIS_CACHE_DB": "0",
        }
    )
    return env


def _run(code: str, port: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=_redis_env(port),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _client():
    import redis

    return redis.Redis(
        host=REDIS_HOST,
        port=int(REDIS_PORT),
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=2,
    )


@pytest.fixture(scope="module", autouse=True)
def _require_runtime():
    if not _runtime_enabled():
        pytest.skip("set RUN_RUNTIME_VALIDATION=1 to run real runtime validation")


@pytest.fixture()
def session_id():
    sid = f"runtime-{uuid.uuid4().hex[:12]}"
    client = _client()
    client.delete(f"session:{sid}")
    yield sid
    client.delete(f"session:{sid}")


WRITE_CODE = """
import os, json
from core.pipeline_context import SessionState, QueryRewriteResult, RecallResult
sid = os.environ["SID"]
state = SessionState.get_or_create(sid)
state.add_round(
    "法规问题",
    "答案",
    QueryRewriteResult(rewritten_query="法规 查询", business_type="regulation", intent="compliance", confidence=0.8),
)
state.lock_evidence(["doc_a", "doc_b"])
state.store_async_clip_result([
    RecallResult(doc_id="img_1", content="图片", score=0.7, source="clip_visual", metadata={"image_uri": "s3://x"})
])
print("WRITE_OK")
"""


READ_CODE = """
import os, json
from core.pipeline_context import SessionState, QueryRewriteResult, RecallResult
sid = os.environ["SID"]
state = SessionState.get_or_create(sid)
round0 = state.dialog_rounds[0]
out = {
    "session_id": state.session_id,
    "rounds": len(state.dialog_rounds),
    "user_input": round0["user_input"],
    "rewrite_type": type(round0["rewrite"]).__name__,
    "rewrite_business": round0["rewrite"].business_type if round0["rewrite"] else None,
    "last_rewrite_type": type(state.last_rewrite_result).__name__ if state.last_rewrite_result else None,
    "locked": state.locked_doc_ids,
    "clip_type": type(state.async_clip_results[0]).__name__ if state.async_clip_results else None,
    "clip_meta": state.async_clip_results[0].metadata if state.async_clip_results else None,
}
print(json.dumps(out, ensure_ascii=False))
"""


MODIFY_CODE = """
import os
from core.pipeline_context import SessionState
sid = os.environ["SID"]
state = SessionState.get_or_create(sid)
state.add_round("追加问题", "追加答案")
print("MODIFY_OK")
"""


def _env_with_sid(sid: str, port: str | None = None) -> dict[str, str]:
    env = _redis_env(port)
    env["SID"] = sid
    return env


def _run_with_sid(code: str, sid: str, port: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=_env_with_sid(sid, port),
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestRedisSessionRuntime:
    def test_cross_process_roundtrip(self, session_id):
        write = _run_with_sid(WRITE_CODE, session_id)
        assert write.returncode == 0, write.stderr
        assert "WRITE_OK" in write.stdout

        read = _run_with_sid(READ_CODE, session_id)
        assert read.returncode == 0, read.stderr
        data = json.loads(read.stdout.strip().splitlines()[-1])
        assert data["session_id"] == session_id
        assert data["rounds"] == 1
        assert data["user_input"] == "法规问题"
        assert data["rewrite_type"] == "QueryRewriteResult"
        assert data["rewrite_business"] == "regulation"
        assert data["last_rewrite_type"] == "QueryRewriteResult"
        assert data["locked"] == ["doc_a", "doc_b"]
        assert data["clip_type"] == "RecallResult"
        assert data["clip_meta"] == {"image_uri": "s3://x"}

    def test_third_process_sees_second_process_update(self, session_id):
        assert _run_with_sid(WRITE_CODE, session_id).returncode == 0
        modify = _run_with_sid(MODIFY_CODE, session_id)
        assert modify.returncode == 0, modify.stderr

        read = _run_with_sid(READ_CODE, session_id)
        data = json.loads(read.stdout.strip().splitlines()[-1])
        assert data["rounds"] == 2

    def test_ttl_is_set_and_refreshed(self, session_id):
        assert _run_with_sid(WRITE_CODE, session_id).returncode == 0
        client = _client()
        ttl_after_write = client.ttl(f"session:{session_id}")
        assert 0 < ttl_after_write <= 7200
        client.expire(f"session:{session_id}", 5)
        _run_with_sid(READ_CODE, session_id)
        assert client.ttl(f"session:{session_id}") > 5

    def test_redis_unavailable_falls_back_to_memory(self, session_id):
        unused_port = "6399"
        code = """
import os
from core.pipeline_context import SessionState
sid = os.environ["SID"]
state = SessionState.get_or_create(sid)
state.add_round("q", "a")
assert len(state.dialog_rounds) == 1
assert SessionState._sessions[sid] is state
print("FALLBACK_OK")
"""
        result = _run_with_sid(code, session_id, port=unused_port)
        assert result.returncode == 0, result.stderr
        assert "FALLBACK_OK" in result.stdout

    def test_malformed_payload_falls_back(self, session_id):
        client = _client()
        client.setex(f"session:{session_id}", 7200, "{this-is-not-json")
        code = """
import os
from core.pipeline_context import SessionState
sid = os.environ["SID"]
assert SessionState._try_get_redis(sid) is None
state = SessionState.get_or_create(sid)
assert state.session_id == sid
print("MALFORMED_OK")
"""
        result = _run_with_sid(code, session_id)
        assert result.returncode == 0, result.stderr
        assert "MALFORMED_OK" in result.stdout

    def test_incompatible_schema_version_is_ignored(self, session_id):
        client = _client()
        client.setex(
            f"session:{session_id}",
            7200,
            json.dumps({"schema_version": 0, "session_id": session_id}),
        )
        code = """
import os
from core.pipeline_context import SessionState
sid = os.environ["SID"]
assert SessionState._try_get_redis(sid) is None
print("SCHEMA_OK")
"""
        result = _run_with_sid(code, session_id)
        assert result.returncode == 0, result.stderr
        assert "SCHEMA_OK" in result.stdout
