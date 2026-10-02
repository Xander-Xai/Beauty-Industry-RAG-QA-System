"""Regression tests for issues raised in the first automated review of PR #17.

Each test fails against the implementation that was reviewed and passes after the
corresponding fix.
"""

from __future__ import annotations

import json

import pytest

from benchmarks.dataset import detect_relevance_level
from benchmarks.runner import MIN_TOP_K, run_configuration

# ── P1: retrieval depth must cover every reported cutoff ────────────────────


def test_top_k_below_largest_cutoff_is_rejected(make_query):
    queries = [make_query("0000", ["alpha passage"])]
    with pytest.raises(ValueError, match="largest reported cutoff"):
        run_configuration("bm25", queries, top_k=MIN_TOP_K - 1)


def test_min_top_k_covers_the_largest_cutoff():
    from benchmarks.models import HIT_KS, MRR_K, NDCG_K, RECALL_KS

    assert MIN_TOP_K == max(max(RECALL_KS), max(HIT_KS), MRR_K, NDCG_K)
    assert MIN_TOP_K == 10


def test_cli_rejects_shallow_top_k():
    from benchmarks.retrieval_benchmark import main

    exit_code = main(["--config", "bm25", "--top-k", "3", "--allow-dirty", "--output-dir", "/tmp/bench-shallow"])
    assert exit_code == 2


# ── P2: stable-id detection must check each field ───────────────────────────


@pytest.mark.parametrize("field", ["doc_id", "chunk_id", "source_id"])
def test_detect_relevance_level_sees_each_stable_id(field):
    assert detect_relevance_level([{field: "x", "contexts": ["a"]}]) == "level1_stable_id"


def test_detect_relevance_level_stays_level2_without_ids():
    assert detect_relevance_level([{"contexts": ["a"]}]) == "level2_normalized_exact_text"


def test_stable_id_dataset_loads_level1_items(tmp_path):
    from benchmarks.dataset import load_queries

    path = tmp_path / "ids.jsonl"
    path.write_text(
        json.dumps(
            {
                "question": "q",
                "contexts": ["alpha"],
                "doc_id": "doc-1",
                "business_type": "regulation",
                "difficulty": "easy",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    queries = load_queries(path)
    assert [item.key for item in queries[0].relevant_items] == ["doc-1"]


# ── P2: coverage must describe the executed subset ──────────────────────────


def test_coverage_reflects_selected_subset(mini_dataset):
    from benchmarks.dataset import bucket_coverage, load_queries

    queries = load_queries(mini_dataset, limit=1)
    coverage = bucket_coverage([query.raw for query in queries])
    assert coverage["sample_count"] == 1
    assert coverage["field_coverage"]["business_type"] == 1
    assert coverage["field_coverage"]["contexts"] == 1


def test_coverage_unavailable_buckets_still_reported(mini_dataset):
    from benchmarks.dataset import bucket_coverage, load_queries

    coverage = bucket_coverage([q.raw for q in load_queries(mini_dataset, limit=1)])
    assert coverage["unavailable_buckets"] == ["visual_required", "complexity"]


# ── P2: blocked artifacts must not claim to be benchmark results ─────────────


def test_blocked_run_metadata_is_not_a_benchmark(tmp_path, monkeypatch):
    """A run where nothing executed must not set results_are_benchmark."""
    from benchmarks import retrieval_benchmark as cli

    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))
    exit_code = cli.main(
        [
            "--config",
            "bm25",
            "--limit",
            "2",
            "--dataset",
            "tests/evaluation/golden_set.jsonl",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 0
    runs = sorted(p for p in tmp_path.iterdir() if p.is_dir())
    assert runs
    metadata = json.loads((runs[-1] / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["results_are_benchmark"] is False
    assert metadata["sample_count"] == 2


# ── P2: Elasticsearch probe settings must follow configuration ───────────────


def test_elastic_port_is_parsed_from_url(monkeypatch):
    from benchmarks import backends
    from common import config as common_config

    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"elasticsearch": {"host": "http://localhost:19200", "index": "idx"}},
    )
    url, index, port, auth = backends._elastic_settings()
    assert url == "http://localhost:19200"
    assert port == 19200
    assert index == "idx"


def test_elastic_probe_uses_configured_credentials(monkeypatch):
    from benchmarks import backends
    from common import config as common_config

    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {
            "elasticsearch": {"host": "http://localhost:9200", "index": "idx", "username": "elastic", "password": "pw"}
        },
    )
    _, _, _, auth = backends._elastic_settings()
    assert auth == ("elastic", "pw")


def test_http_status_is_reachable_not_unreachable(monkeypatch):
    """An HTTP 401 proves the service is reachable; it is an auth/index problem."""
    import urllib.error

    from benchmarks import backends

    def _raise_401(request, timeout=None):
        raise urllib.error.HTTPError("http://x", 401, "Unauthorized", None, None)

    monkeypatch.setattr(backends.urllib.request, "urlopen", _raise_401)
    ok, code = backends._http_get("http://localhost:9200/idx/_count")
    assert ok == "http"
    assert code == 401


# ── P2: dense probe must use the configured collection ───────────────────────


def test_qdrant_probe_uses_configured_text_collection(monkeypatch):
    from benchmarks import backends
    from common import config as common_config

    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {
            "qdrant": {"host": "qdrant", "port": 6333},
            "embedding": {"text": {"collection": "rag_text_custom"}},
        },
    )
    _, _, collection = backends._qdrant_settings()
    assert collection == "rag_text_custom"


# ── Round 2: HTTP sentinel, fabricated stage latency, credential precedence ──


def test_qdrant_http_error_is_not_treated_as_available(monkeypatch):
    """An HTTP 401/404 for the collection is a reachable service, not a usable one."""
    import urllib.error

    from benchmarks import backends

    def _raise_401(request, timeout=None):
        raise urllib.error.HTTPError("http://x", 401, "Unauthorized", None, None)

    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: True)
    monkeypatch.setattr(backends.urllib.request, "urlopen", _raise_401)
    monkeypatch.setattr(backends, "_model_weights_available", lambda: (True, "model"))
    availability = backends.probe_dense()
    assert availability.available is False
    assert availability.reason == backends.REASON_SERVICE_NO_DATA
    assert "401" in (availability.detail or "")


def test_per_stage_latency_is_not_fabricated(make_query):
    """Untimed stages must stay None instead of mirroring the end-to-end time."""
    from benchmarks.latency import stage_availability
    from benchmarks.models import LATENCY_STAGES
    from benchmarks.runner import run_configuration

    queries = [make_query("0000", ["alpha passage"])]
    from tests.benchmark.conftest import FixtureRetriever

    retriever = FixtureRetriever({"0000": ["alpha passage"]})
    run = run_configuration("hybrid_rrf", queries, retriever_factory=lambda name: retriever)
    assert run.executed
    latency = run.results[0].latency_ms
    assert latency["total_retrieval_ms"] is not None
    for stage in LATENCY_STAGES:
        if stage != "total_retrieval_ms":
            assert latency[stage] is None, stage
    availability = stage_availability([r.latency_ms for r in run.results])
    assert availability["bm25_ms"] == 0
    assert availability["total_retrieval_ms"] == 1


def test_environment_credentials_take_precedence_over_config(monkeypatch):
    """The probe must mirror BM25Retriever: env first, config.json as fallback."""
    from benchmarks import backends
    from common import config as common_config

    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {
            "elasticsearch": {
                "host": "http://localhost:9200",
                "index": "idx",
                "username": "from-config",
                "password": "cfg-pass",
            }
        },
    )
    monkeypatch.setenv("ELASTICSEARCH_USERNAME", "from-env")
    monkeypatch.setenv("ELASTICSEARCH_PASSWORD", "env-pass")
    _, _, _, auth = backends._elastic_settings()
    assert auth == ("from-env", "env-pass")


def test_config_credentials_used_when_env_absent(monkeypatch):
    from benchmarks import backends
    from common import config as common_config

    monkeypatch.delenv("ELASTICSEARCH_USERNAME", raising=False)
    monkeypatch.delenv("ELASTICSEARCH_PASSWORD", raising=False)
    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {
            "elasticsearch": {
                "host": "http://localhost:9200",
                "index": "idx",
                "username": "from-config",
                "password": "cfg-pass",
            }
        },
    )
    _, _, _, auth = backends._elastic_settings()
    assert auth == ("from-config", "cfg-pass")


# ── Round 3: effective config hash, per-query failures, empty collection ─────


def test_config_hash_covers_effective_runtime_config(monkeypatch):
    """Two runs against different collections must not share a config hash."""
    from benchmarks.provenance import effective_retrieval_config, sha256_json
    from common import config as common_config

    base = {
        "retrieval": {"rrf": {"k": 60}},
        "qdrant": {"host": "qdrant", "collections": {}},
        "embedding": {"text": {"collection": "rag_text_768"}},
    }
    monkeypatch.setattr(common_config, "get_config_dict", lambda: base)
    first = sha256_json({"configs": ["bm25"], "top_k": 10, "effective_config": effective_retrieval_config()})

    changed = {
        "retrieval": {"rrf": {"k": 60}},
        "qdrant": {"host": "qdrant", "collections": {}},
        "embedding": {"text": {"collection": "rag_text_custom"}},
    }
    monkeypatch.setattr(common_config, "get_config_dict", lambda: changed)
    second = sha256_json({"configs": ["bm25"], "top_k": 10, "effective_config": effective_retrieval_config()})
    assert first != second


def test_effective_config_snapshot_has_no_secret_values(monkeypatch):
    from benchmarks.provenance import effective_retrieval_config
    from common import config as common_config

    monkeypatch.setenv("ELASTICSEARCH_PASSWORD", "hunter2")
    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"elasticsearch": {"host": "h", "index": "i", "password": "cfg-hunter2"}},
    )
    snapshot = effective_retrieval_config()
    text = json.dumps(snapshot)
    assert "hunter2" not in text
    assert snapshot["elasticsearch"]["password_set"] is True


def test_empty_qdrant_collection_is_rejected(monkeypatch):
    from benchmarks import backends

    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: True)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (True, {"result": {"points_count": 0}}))
    monkeypatch.setattr(backends, "_model_weights_available", lambda: (True, "model"))
    availability = backends.probe_dense()
    assert availability.available is False
    assert availability.reason == backends.REASON_SERVICE_NO_DATA
    assert "0 points" in (availability.detail or "")


def test_non_empty_qdrant_collection_with_weights_is_available(monkeypatch):
    from benchmarks import backends

    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: True)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (True, {"result": {"points_count": 42}}))
    monkeypatch.setattr(backends, "_model_weights_available", lambda: (True, "model"))
    assert backends.probe_dense().available is True


def test_one_failed_query_does_not_discard_measurements(make_query):
    """A failing query is recorded; other queries still produce metrics."""
    from benchmarks.runner import run_configuration, summarize_run

    class FlakyRetriever:
        name = "flaky"

        def retrieve(self, query, top_k):
            if query.sample_id == "0001":
                raise TimeoutError("backend timeout")
            from benchmarks.relevance import text_key

            return [
                __import__("benchmarks.models", fromlist=["RetrievedItem"]).RetrievedItem(
                    rank=1, key=text_key("alpha passage"), score=1.0, source="flaky"
                )
            ]

    queries = [make_query("0000", ["alpha passage"]), make_query("0001", ["beta passage"])]
    run = run_configuration("bm25", queries, retriever_factory=lambda name: FlakyRetriever())
    assert run.executed
    assert len(run.results) == 1
    assert len(run.failures) == 1
    assert run.failures[0]["sample_id"] == "0001"
    assert run.failures[0]["error_type"] == "TimeoutError"
    summary = summarize_run([run])
    assert summary["configs"]["bm25"]["failure_count"] == 1


def test_all_failed_queries_block_the_configuration(make_query):
    from benchmarks.runner import run_configuration

    class DeadRetriever:
        name = "dead"

        def retrieve(self, query, top_k):
            raise ConnectionError("service down")

    queries = [make_query("0000", ["alpha passage"])]
    run = run_configuration("bm25", queries, retriever_factory=lambda name: DeadRetriever())
    assert run.executed is False
    assert run.results == []
    assert run.failures
    assert "failed during retrieval" in run.outcome.reason
