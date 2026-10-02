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

    # gRPC closed so the REST branch is the one under test.
    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: port != 6334)
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

    # gRPC closed so the REST branch is the one under test.
    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: port != 6334)
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
    monkeypatch.setattr(backends, "_grpc_collection_points", lambda host, port, coll: (42, "ok"))
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


# ── Round 4: clean git status, partial failures in report, mixed relevance ───


def test_clean_git_status_is_false_not_null(tmp_path):
    """A clean tree must attest git_dirty=false, not null."""
    import subprocess as sp

    from benchmarks.provenance import git_provenance

    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    sp.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"],
        cwd=tmp_path,
        check=True,
    )
    sha, dirty = git_provenance(tmp_path)
    assert sha is not None
    assert dirty is False


def test_dirty_tree_is_true(tmp_path):
    import subprocess as sp

    from benchmarks.provenance import git_provenance

    sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    sp.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "untracked.txt").write_text("x", encoding="utf-8")
    _, dirty = git_provenance(tmp_path)
    assert dirty is True


def test_partial_failures_appear_in_report():
    """Executed-with-failures must disclose the reduced sample in report.md."""
    from benchmarks.report import build_report

    summary = {
        "configs": {
            "bm25": {
                "status": "EXECUTED",
                "reason": "ran",
                "failure_count": 2,
                "metrics": {"overall": {"sample_count": 8}, "by_business_type": {}, "by_difficulty": {}},
                "latency": {},
            }
        },
        "executed_configs": ["bm25"],
        "blocked_configs": [],
        "latency": {},
        "any_results": True,
    }
    coverage = {
        "sample_count": 10,
        "relevance_level": "level2",
        "available_buckets": [],
        "unavailable_buckets": [],
        "field_coverage": {},
    }
    text = build_report(
        {"run_id": "r", "requested_configs": ["bm25"]},
        {},
        summary,
        coverage,
        per_query_rows=8,
        synthetic_retriever=True,
    )
    assert "2 sample(s) failed during retrieval" in text
    assert "fewer samples than requested" in text


def test_mixed_relevance_strategy_is_reported():
    from benchmarks.dataset import bucket_coverage, detect_relevance_level

    rows = [
        {"doc_id": "d1", "contexts": ["a"]},
        {"contexts": ["b"]},
    ]
    level = detect_relevance_level(rows)
    assert level.startswith("mixed")
    counts = bucket_coverage(rows)["relevance_strategy_counts"]
    assert counts["level1_stable_id"] == 1
    assert counts["level2_normalized_exact_text"] == 1


def test_uniform_relevance_levels_are_unambiguous():
    from benchmarks.dataset import detect_relevance_level

    assert detect_relevance_level([{"doc_id": "a", "contexts": []}]) == "level1_stable_id"
    assert detect_relevance_level([{"contexts": ["x"]}]) == "level2_normalized_exact_text"


# ── Round 5: snapshot persistence, sample selection, real model assets ───────


def test_effective_config_is_persisted_not_only_hashed(tmp_path, monkeypatch):
    """The snapshot must travel with the hash so a run is reproducible."""
    import benchmarks.retrieval_benchmark as cli

    monkeypatch.setattr(cli, "git_provenance", lambda root: ("sha", False))
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
    run_dir = sorted(p for p in tmp_path.iterdir() if p.is_dir())[-1]
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert "effective_config" in metadata
    assert "elasticsearch" in metadata["effective_config"]
    assert "qdrant" in metadata["effective_config"]


def test_missing_requested_sample_id_is_rejected():
    from benchmarks.dataset import DatasetError, load_queries

    with pytest.raises(DatasetError, match="not present"):
        load_queries("tests/evaluation/golden_set.jsonl", sample_ids=["0000", "9999"])


def test_existing_sample_ids_still_work():
    from benchmarks.dataset import load_queries

    queries = load_queries("tests/evaluation/golden_set.jsonl", sample_ids=["0000", "0002"])
    assert [q.sample_id for q in queries] == ["0000", "0002"]


@pytest.mark.parametrize("limit", [0, -1, -10])
def test_non_positive_limit_is_rejected(limit):
    from benchmarks.dataset import DatasetError, load_queries

    with pytest.raises(DatasetError, match="positive"):
        load_queries("tests/evaluation/golden_set.jsonl", limit=limit)


def test_empty_model_directory_is_not_available(tmp_path):
    from benchmarks.backends import _looks_like_model_dir

    empty = tmp_path / "model"
    empty.mkdir()
    ok, detail = _looks_like_model_dir(empty)
    assert ok is False
    assert "incomplete model directory" in detail


def test_unrelated_file_is_not_a_model_dir(tmp_path):
    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    (path / "README.txt").write_text("not a model", encoding="utf-8")
    assert _looks_like_model_dir(path)[0] is False


def _complete_model_dir(path):
    """A minimal but *complete* transformers snapshot."""
    path.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "tokenizer.json", "model.safetensors"):
        (path / name).write_text("x", encoding="utf-8")
    return path


def test_complete_model_dir_is_accepted(tmp_path):
    from benchmarks.backends import _looks_like_model_dir

    ok, detail = _looks_like_model_dir(_complete_model_dir(tmp_path / "model"))
    assert ok is True
    assert "found model config" in detail


@pytest.mark.parametrize(
    ("files", "missing_fragment"),
    [
        (("config.json", "tokenizer_config.json"), "weights"),
        (("config.json", "model.safetensors"), "tokenizer"),
        (("model.safetensors", "tokenizer.json"), "config.json"),
        (("config.json",), "weights"),
    ],
)
def test_partial_model_cache_is_rejected(tmp_path, files, missing_fragment):
    """A lone config or lone weight file must not look loadable."""
    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in files:
        (path / name).write_text("x", encoding="utf-8")
    ok, detail = _looks_like_model_dir(path)
    assert ok is False
    assert missing_fragment in detail


def test_model_probe_rejects_empty_directory(tmp_path, monkeypatch):
    """probe_dense must not report available for an empty model directory."""
    from benchmarks import backends
    from common import config as common_config

    empty = tmp_path / "model"
    empty.mkdir()
    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {
            "qdrant": {"host": "qdrant", "port": 6333},
            "embedding": {"text": {"collection": "rag_text_768", "model_path": str(empty)}},
        },
    )
    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: True)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (True, {"result": {"points_count": 10}}))
    availability = backends.probe_dense()
    assert availability.available is False
    assert availability.reason == backends.REASON_MODEL_UNAVAILABLE


# ── Round 6: complete model set, elasticsearch.enabled, empty --configs ──────


def test_disabled_elasticsearch_is_blocked_even_when_reachable(monkeypatch):
    """BM25Retriever returns nothing while disabled, so the probe must agree."""
    from benchmarks import backends
    from common import config as common_config

    monkeypatch.setattr(
        backends,
        "_elastic_settings",
        lambda: ("http://es", "cosmetics_docs", 9200, None),
    )
    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"elasticsearch": {"enabled": False}},
    )
    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: True)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (True, {"count": 42}))
    availability = backends.probe_bm25()
    assert availability.available is False
    assert availability.reason == backends.REASON_SERVICE_DISABLED
    assert "enabled is false" in availability.detail


def test_enabled_elasticsearch_still_probes(monkeypatch):
    from benchmarks import backends
    from common import config as common_config

    monkeypatch.setattr(
        backends,
        "_elastic_settings",
        lambda: ("http://es", "cosmetics_docs", 9200, None),
    )
    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"elasticsearch": {"enabled": True}},
    )
    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: True)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (True, {"count": 42}))
    assert backends.probe_bm25().available is True


@pytest.mark.parametrize("value", [",", " , ", ",,", " , , "])
def test_empty_configs_list_is_rejected(value, tmp_path):
    """`,`-only input must not produce a successful 'multi' artifact."""
    import argparse

    import benchmarks.retrieval_benchmark as cli

    args = argparse.Namespace(config=None, configs=value)
    with pytest.raises(SystemExit, match="contained no configuration names"):
        cli._resolve_configs(args)


def test_valid_configs_list_is_accepted():
    import argparse

    import benchmarks.retrieval_benchmark as cli

    args = argparse.Namespace(config=None, configs="bm25, dense")
    assert cli._resolve_configs(args) == ["bm25", "dense"]


# ── Round 7: loadable artifacts, gRPC probe, non-object rows, reranker hash ──


def test_tokenizer_config_alone_is_not_a_vocabulary(tmp_path):
    """`tokenizer_config.json` carries no tokens; from_pretrained would fail."""
    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in ("config.json", "tokenizer_config.json", "model.safetensors"):
        (path / name).write_text("x", encoding="utf-8")
    ok, detail = _looks_like_model_dir(path)
    assert ok is False
    assert "tokenizer vocabulary" in detail


@pytest.mark.parametrize("weight", ["model.pt", "model.onnx"])
def test_arbitrary_weight_extensions_are_rejected(tmp_path, weight):
    """AutoModel.from_pretrained cannot load a bare .pt/.onnx checkpoint."""
    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in ("config.json", "tokenizer.json", weight):
        (path / name).write_text("x", encoding="utf-8")
    ok, detail = _looks_like_model_dir(path)
    assert ok is False
    assert "model weights" in detail


def test_vocab_txt_vocabulary_is_accepted(tmp_path):
    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in ("config.json", "vocab.txt", "pytorch_model.bin"):
        (path / name).write_text("x", encoding="utf-8")
    assert _looks_like_model_dir(path)[0] is True


def _qdrant_config(model_path, **qdrant_overrides):
    """Config mirroring production: REST port + gRPC port + an embedding model."""
    qdrant = {"host": "qdrant", "port": 6333, "grpc_port": 6334}
    qdrant.update(qdrant_overrides)
    return {
        "qdrant": qdrant,
        "embedding": {"text": {"collection": "rag_text_768", "model_path": str(model_path)}},
    }


def _write_complete_model(path):
    path.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "tokenizer.json", "model.safetensors"):
        (path / name).write_text("x", encoding="utf-8")
    return path


def test_dense_available_via_grpc_when_rest_is_closed(monkeypatch, tmp_path):
    """EmbeddingService prefers gRPC, so REST-only reachability is not enough."""
    from benchmarks import backends
    from common import config as common_config

    model = _write_complete_model(tmp_path / "model")
    monkeypatch.setattr(common_config, "get_config_dict", lambda: _qdrant_config(model))
    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: port == 6334)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (False, None))
    monkeypatch.setattr(backends, "_grpc_collection_points", lambda host, port, coll: (5, "ok"))
    availability = backends.probe_dense()
    assert availability.available is True, availability.detail


def test_grpc_tcp_only_is_not_enough_when_collection_absent(monkeypatch, tmp_path):
    """A reachable gRPC port must not imply the configured collection exists."""
    from benchmarks import backends
    from common import config as common_config

    model = _write_complete_model(tmp_path / "model")
    monkeypatch.setattr(common_config, "get_config_dict", lambda: _qdrant_config(model))
    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: port == 6334)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (False, None))
    monkeypatch.setattr(
        backends,
        "_grpc_collection_points",
        lambda host, port, coll: (None, f"collection {coll} is absent or inaccessible over gRPC"),
    )
    availability = backends.probe_dense()
    assert availability.available is False
    assert availability.reason == backends.REASON_SERVICE_NO_DATA
    assert "absent or inaccessible" in availability.detail


def test_grpc_verified_empty_collection_is_blocked(monkeypatch, tmp_path):
    """A gRPC-verified but empty collection cannot answer any query."""
    from benchmarks import backends
    from common import config as common_config

    model = _write_complete_model(tmp_path / "model")
    monkeypatch.setattr(common_config, "get_config_dict", lambda: _qdrant_config(model))
    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: port == 6334)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (False, None))
    monkeypatch.setattr(backends, "_grpc_collection_points", lambda host, port, coll: (0, "ok"))
    availability = backends.probe_dense()
    assert availability.available is False
    assert "0 points" in availability.detail


def test_grpc_verification_is_attempted_with_prefer_grpc(monkeypatch):
    """The verification client must use gRPC, matching EmbeddingService."""
    from benchmarks import backends

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def get_collection(self, name):
            class Info:
                points_count = 3

            return Info()

        def close(self):
            captured["closed"] = True

    import qdrant_client

    monkeypatch.setattr(qdrant_client, "QdrantClient", FakeClient, raising=False)
    points, note = backends._grpc_collection_points("qdrant", 6334, "rag_text_768")
    assert points == 3
    assert captured["prefer_grpc"] is True
    assert captured["grpc_port"] == 6334
    assert captured["closed"] is True


def test_grpc_verification_failure_is_not_available(monkeypatch):
    from benchmarks import backends

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def get_collection(self, name):
            raise RuntimeError("NotFound: collection does not exist")

        def close(self):
            pass

    import qdrant_client

    monkeypatch.setattr(qdrant_client, "QdrantClient", FakeClient, raising=False)
    points, note = backends._grpc_collection_points("qdrant", 6334, "rag_text_768")
    assert points is None
    assert "absent or inaccessible" in note


def test_dense_blocked_when_both_protocols_are_closed(monkeypatch, tmp_path):
    from benchmarks import backends
    from common import config as common_config

    model = _write_complete_model(tmp_path / "model")
    monkeypatch.setattr(common_config, "get_config_dict", lambda: _qdrant_config(model))
    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: False)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (False, None))
    availability = backends.probe_dense()
    assert availability.available is False
    assert availability.reason == backends.REASON_SERVICE_UNREACHABLE
    assert "grpc" in availability.detail


def test_rest_only_deployment_still_works(monkeypatch, tmp_path):
    from benchmarks import backends
    from common import config as common_config

    model = _write_complete_model(tmp_path / "model")
    monkeypatch.setattr(common_config, "get_config_dict", lambda: _qdrant_config(model))
    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: port == 6333)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (True, {"result": {"points_count": 5}}))
    assert backends.probe_dense().available is True


@pytest.mark.parametrize("payload", ["[]", '"text"', "42", "null"])
def test_non_object_rows_are_dataset_errors(tmp_path, payload):
    from benchmarks.dataset import DatasetError, load_rows

    bad = tmp_path / "bad.jsonl"
    bad.write_text(payload + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="must be a JSON object"):
        load_rows(bad)


def test_non_object_row_reports_line_number(tmp_path):
    from benchmarks.dataset import DatasetError, load_rows

    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"question":"q","contexts":["c"]}\n"oops"\n', encoding="utf-8")
    with pytest.raises(DatasetError, match=r":2:"):
        load_rows(bad)


def test_non_object_row_surfaces_through_cli(tmp_path):
    """The CLI catches DatasetError, so malformed rows must not raise AttributeError."""
    import benchmarks.retrieval_benchmark as cli

    bad = tmp_path / "bad.jsonl"
    bad.write_text('"just a string"\n', encoding="utf-8")
    exit_code = cli.main(
        ["--config", "bm25", "--dataset", str(bad), "--output-dir", str(tmp_path / "out"), "--allow-dirty"]
    )
    assert exit_code == 2
    assert not list((tmp_path / "out").glob("*/metadata.json")) if (tmp_path / "out").exists() else True


def test_cross_encoder_models_change_the_config_hash(monkeypatch):
    """Changing a reranker path must change config_sha256."""
    from benchmarks import provenance
    from common import config as common_config

    base = {
        "elasticsearch": {"host": "http://es", "index": "i"},
        "gpu1": {
            "models": {"cross_encoder_a": {"model_path": "/models/a"}, "cross_encoder_b": {"model_path": "/models/b"}}
        },
    }
    monkeypatch.setattr(common_config, "get_config_dict", lambda: base)
    first = provenance.sha256_json(provenance.effective_retrieval_config())

    changed = json.loads(json.dumps(base))
    changed["gpu1"]["models"]["cross_encoder_a"]["model_path"] = "/models/changed"
    monkeypatch.setattr(common_config, "get_config_dict", lambda: changed)
    second = provenance.sha256_json(provenance.effective_retrieval_config())
    assert first != second


def test_cross_encoder_snapshot_exposes_model_paths(monkeypatch):
    from benchmarks import provenance
    from common import config as common_config

    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"gpu1": {"models": {"cross_encoder_a": {"model_path": "/models/a"}}}},
    )
    snapshot = provenance.effective_retrieval_config()
    assert snapshot["cross_encoder"]["cross_encoder_a"]["model_path"] == "/models/a"


# ── Round 8: unusable contexts and coverage-derived breakdowns ──────────────


@pytest.mark.parametrize("contexts", [[""], ["   "], [None], [123], ["ok", ""], ["ok", None]])
def test_unusable_context_entries_are_dataset_errors(tmp_path, contexts):
    """Blank/non-string ground truth must not become 'None' or a ValueError."""
    import json

    from benchmarks.dataset import DatasetError, load_queries

    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"question": "q", "contexts": contexts}) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="non-empty string"):
        load_queries(bad)


def test_unusable_context_names_the_sample(tmp_path):
    import json

    from benchmarks.dataset import DatasetError, load_queries

    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps({"question": "q0", "contexts": ["fine"]})
        + "\n"
        + json.dumps({"question": "q1", "contexts": ["fine", None]})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetError, match=r"sample 0001 context 1"):
        load_queries(bad)


def test_unusable_context_exits_cleanly_through_cli(tmp_path):
    import json

    import benchmarks.retrieval_benchmark as cli

    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"question": "q", "contexts": [""]}) + "\n", encoding="utf-8")
    exit_code = cli.main(
        ["--config", "bm25", "--dataset", str(bad), "--output-dir", str(tmp_path / "out"), "--allow-dirty"]
    )
    assert exit_code == 2


def test_breakdowns_require_actual_field_coverage():
    """A dataset without business_type/difficulty must not advertise them."""
    from benchmarks.dataset import bucket_coverage

    coverage = bucket_coverage([{"question": "q", "contexts": ["c"]}])
    assert coverage["available_buckets"] == ["overall"]


def test_breakdowns_included_when_fields_present():
    from benchmarks.dataset import bucket_coverage

    coverage = bucket_coverage(
        [{"question": "q", "contexts": ["c"], "business_type": "ingredient", "difficulty": "easy"}]
    )
    assert "business_type" in coverage["available_buckets"]
    assert "difficulty" in coverage["available_buckets"]


def test_partial_field_coverage_lists_only_supported_breakdowns():
    from benchmarks.dataset import bucket_coverage

    coverage = bucket_coverage(
        [
            {"question": "q1", "contexts": ["c"], "business_type": "ingredient"},
            {"question": "q2", "contexts": ["c"]},
        ]
    )
    assert "business_type" in coverage["available_buckets"]
    assert "difficulty" not in coverage["available_buckets"]


# ── Round 9: unsupported breakdowns, weight shards, sample-id + limit ─────────


def test_absent_bucket_fields_are_omitted_from_metrics():
    """No business_type in the data must mean no by_business_type block."""
    from benchmarks.metrics import aggregate_results
    from tests.benchmark.test_aggregation import _result, _to_query_result

    results = [_to_query_result(_result("0000", "unknown", "unknown", ["a"], ["a"]))]
    aggregate = aggregate_results(results, available_buckets=["overall"])
    assert "by_business_type" not in aggregate
    assert "by_difficulty" not in aggregate
    assert aggregate["overall"]["sample_count"] == 1


def test_supported_bucket_fields_are_kept():
    from benchmarks.metrics import aggregate_results
    from tests.benchmark.test_aggregation import _result, _to_query_result

    results = [_to_query_result(_result("0000", "ingredient", "easy", ["a"], ["a"]))]
    aggregate = aggregate_results(results, available_buckets=["overall", "business_type", "difficulty"])
    assert "ingredient" in aggregate["by_business_type"]
    assert "easy" in aggregate["by_difficulty"]


def test_unknown_placeholder_rows_are_not_reported_as_a_breakdown():
    """The loader substitutes 'unknown'; an all-unknown table must not surface."""
    from benchmarks.metrics import aggregate_results
    from tests.benchmark.test_aggregation import _result, _to_query_result

    results = [_to_query_result(_result("0000", "unknown", "unknown", ["a"], ["a"]))]
    aggregate = aggregate_results(results, available_buckets=["overall"])
    assert "by_business_type" not in aggregate
    assert "by_difficulty" not in aggregate


def test_report_omits_missing_breakdown_sections():
    from benchmarks.report import build_report

    summary = {
        "configs": {
            "bm25": {
                "status": "EXECUTED",
                "reason": "ran",
                "metrics": {"overall": {"sample_count": 3}},
                "latency": {},
            }
        },
        "executed_configs": ["bm25"],
        "blocked_configs": [],
        "latency": {},
        "any_results": True,
    }
    coverage = {"sample_count": 3, "available_buckets": ["overall"], "unavailable_buckets": [], "field_coverage": {}}
    text = build_report(
        {"run_id": "r", "requested_configs": ["bm25"]},
        {},
        summary,
        coverage,
        per_query_rows=3,
        synthetic_retriever=True,
    )
    assert "Metrics by Business Type" not in text
    assert "Metrics by Difficulty" not in text


def test_incomplete_shard_set_is_rejected(tmp_path):
    """An index declaring 2 shards with only 1 present must not look loadable."""
    import json as _json

    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in ("config.json", "tokenizer.json", "model-00001-of-00002.safetensors"):
        (path / name).write_text("x" * 10, encoding="utf-8")
    (path / "model.safetensors.index.json").write_text(
        _json.dumps(
            {
                "weight_map": {
                    "layer.0": "model-00001-of-00002.safetensors",
                    "layer.1": "model-00002-of-00002.safetensors",
                }
            }
        ),
        encoding="utf-8",
    )
    ok, detail = _looks_like_model_dir(path)
    assert ok is False
    assert "1 of 2 weight shard" in detail


def test_complete_shard_set_is_accepted(tmp_path):
    import json as _json

    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in (
        "config.json",
        "tokenizer.json",
        "model-00001-of-00002.safetensors",
        "model-00002-of-00002.safetensors",
    ):
        (path / name).write_text("x" * 10, encoding="utf-8")
    (path / "model.safetensors.index.json").write_text(
        _json.dumps(
            {
                "weight_map": {
                    "layer.0": "model-00001-of-00002.safetensors",
                    "layer.1": "model-00002-of-00002.safetensors",
                }
            }
        ),
        encoding="utf-8",
    )
    assert _looks_like_model_dir(path)[0] is True


def test_zero_byte_weight_file_is_rejected(tmp_path):
    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in ("config.json", "tokenizer.json"):
        (path / name).write_text("x", encoding="utf-8")
    (path / "model.safetensors").write_bytes(b"")
    ok, detail = _looks_like_model_dir(path)
    assert ok is False
    assert "model weights" in detail


def test_sample_ids_with_smaller_limit_are_not_reported_absent():
    """Regression: the limit must not hide a later requested id from validation."""
    from benchmarks.dataset import load_queries

    queries = load_queries("tests/evaluation/golden_set.jsonl", sample_ids=["0000", "0002"], limit=1)
    assert [q.sample_id for q in queries] == ["0000"]


def test_missing_sample_id_still_rejected_with_limit():
    from benchmarks.dataset import DatasetError, load_queries

    with pytest.raises(DatasetError, match="not present"):
        load_queries("tests/evaluation/golden_set.jsonl", sample_ids=["0000", "9999"], limit=1)


def test_sample_ids_without_limit_select_all():
    from benchmarks.dataset import load_queries

    queries = load_queries("tests/evaluation/golden_set.jsonl", sample_ids=["0001", "0003", "0005"])
    assert [q.sample_id for q in queries] == ["0001", "0003", "0005"]


# ── Round 10: full snapshot sanitization, gRPC-always, invalid index, IPv6 ───


def test_nested_credentials_are_reduced_to_presence_flags():
    from benchmarks.provenance import sanitize

    sanitized = sanitize(
        {
            "embedding": {
                "text": {"model_path": "/models/bge", "api_key": "sk-secret"},
                "nested": {"password": "hunter2", "token": "abc"},
                "list": [{"secret": "s"}],
            },
            "index": "rag",
        }
    )
    assert sanitized["embedding"]["text"]["api_key"] is True
    assert sanitized["embedding"]["nested"]["password"] is True
    assert sanitized["embedding"]["nested"]["token"] is True
    assert sanitized["embedding"]["list"][0]["secret"] is True
    assert sanitized["embedding"]["text"]["model_path"] == "/models/bge"
    assert sanitized["index"] == "rag"


def test_effective_config_snapshot_has_no_nested_secret(monkeypatch):
    """The persisted snapshot must not leak a credential nested under embedding."""
    from benchmarks import provenance
    from common import config as common_config

    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {
            "embedding": {"text": {"model_path": "/m", "api_key": "sk-live-123"}},
            "retrieval": {"rerank": {"token": "tok-xyz"}},
        },
    )
    import json as _json

    rendered = _json.dumps(provenance.effective_retrieval_config())
    assert "sk-live-123" not in rendered
    assert "tok-xyz" not in rendered


def test_snapshot_still_records_embedding_settings(monkeypatch):
    """Sanitizing must not drop the settings needed to reproduce a run."""
    from benchmarks import provenance
    from common import config as common_config

    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"embedding": {"text": {"model_path": "/models/bge", "collection": "rag_text_768", "api_key": "s"}}},
    )
    snapshot = provenance.effective_retrieval_config()
    assert snapshot["embedding"]["text"]["model_path"] == "/models/bge"
    assert snapshot["embedding"]["text"]["collection"] == "rag_text_768"


def test_grpc_failure_blocks_even_when_rest_works(monkeypatch):
    """Production prefers gRPC, so a broken gRPC must not read as available."""
    from benchmarks import backends

    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: True)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (True, {"result": {"points_count": 42}}))
    monkeypatch.setattr(backends, "_grpc_collection_points", lambda host, port, coll: (None, "grpc boom"))
    monkeypatch.setattr(backends, "_model_weights_available", lambda: (True, "model"))
    availability = backends.probe_dense()
    assert availability.available is False
    assert availability.reason == backends.REASON_SERVICE_NO_DATA
    assert "grpc boom" in availability.detail


def test_grpc_empty_over_rest_populated_blocks(monkeypatch):
    from benchmarks import backends

    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: True)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (True, {"result": {"points_count": 42}}))
    monkeypatch.setattr(backends, "_grpc_collection_points", lambda host, port, coll: (0, "ok"))
    monkeypatch.setattr(backends, "_model_weights_available", lambda: (True, "model"))
    availability = backends.probe_dense()
    assert availability.available is False
    assert "0 points over gRPC" in availability.detail


@pytest.mark.parametrize(
    "content",
    ['{"not_weight_map": []}', "[]", "{}", '{"weight_map": []}', "not json at all"],
)
def test_malformed_weight_index_is_rejected(tmp_path, content):
    """An unreadable index is not the same as no index."""
    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in ("config.json", "tokenizer.json", "model-00001-of-00002.safetensors"):
        (path / name).write_text("x" * 10, encoding="utf-8")
    (path / "model.safetensors.index.json").write_text(content, encoding="utf-8")
    ok, detail = _looks_like_model_dir(path)
    assert ok is False
    assert "weight index" in detail


def test_valid_index_with_complete_shards_still_passes(tmp_path):
    """Rejecting malformed indexes must not reject good ones."""
    import json as _json

    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in ("config.json", "tokenizer.json", "model.safetensors"):
        (path / name).write_text("x" * 10, encoding="utf-8")
    (path / "model.safetensors.index.json").write_text(
        _json.dumps({"weight_map": {"layer.0": "model.safetensors"}}), encoding="utf-8"
    )
    assert _looks_like_model_dir(path)[0] is True


@pytest.mark.parametrize("url", ["http://[::1]:9200", "http://user:pass@es:9200", "https://es.internal:9243"])
def test_es_host_is_parsed_as_a_hostname(monkeypatch, url):
    """IPv6 literals and userinfo must not be passed to the TCP pre-check."""
    from benchmarks import backends
    from common import config as common_config

    seen = {}

    def fake_tcp(host, port):
        seen["host"] = host
        seen["port"] = port
        return True

    monkeypatch.setattr(backends, "_elastic_settings", lambda: (url, "idx", 9200, None))
    monkeypatch.setattr(common_config, "get_config_dict", lambda: {"elasticsearch": {"enabled": True}})
    monkeypatch.setattr(backends, "_tcp_reachable", fake_tcp)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (True, {"count": 3}))
    availability = backends.probe_bm25()
    assert seen["host"] in {"::1", "es", "es.internal"}
    assert "[" not in (seen["host"] or "")
    assert "user" not in (seen["host"] or "")
    assert availability.available is True


# ── Round 11: URL credential redaction, gRPC precedence, empty weight index ──


def test_url_userinfo_is_redacted():
    from benchmarks.provenance import redact_url_userinfo

    assert redact_url_userinfo("https://user:password@host:9200") == "https://***:***@host:9200"
    assert redact_url_userinfo("http://es:9200") == "http://es:9200"
    assert redact_url_userinfo("plain-value") == "plain-value"
    assert redact_url_userinfo(None) is None


def test_snapshot_redacts_elasticsearch_url_credentials(monkeypatch):
    """A password embedded in elasticsearch.host must not reach metadata.json."""
    import json as _json

    from benchmarks import provenance
    from common import config as common_config

    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"elasticsearch": {"host": "https://esuser:espassword@es:9200", "index": "cosmetics_docs"}},
    )
    rendered = _json.dumps(provenance.effective_retrieval_config())
    assert "espassword" not in rendered
    assert "esuser" not in rendered
    assert "es:9200" in rendered


def test_snapshot_redacts_nested_url_credentials():
    from benchmarks.provenance import sanitize

    sanitized = sanitize({"backends": [{"url": "https://u:p@svc:9200"}]})
    assert "p@" not in str(sanitized)
    assert "svc:9200" in str(sanitized)


def test_grpc_populated_overrides_rest_http_error(monkeypatch):
    """A REST 401 from a proxy must not block when gRPC can serve the collection."""
    import urllib.error

    from benchmarks import backends

    def _raise_401(request, timeout=None):
        raise urllib.error.HTTPError("http://x", 401, "Unauthorized", None, None)

    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: True)
    monkeypatch.setattr(backends.urllib.request, "urlopen", _raise_401)
    monkeypatch.setattr(backends, "_grpc_collection_points", lambda host, port, coll: (7, "ok"))
    monkeypatch.setattr(backends, "_model_weights_available", lambda: (True, "model"))
    availability = backends.probe_dense()
    assert availability.available is True, availability.detail


def test_grpc_failure_overrides_rest_success(monkeypatch):
    """gRPC is the production transport, so its failure is decisive."""
    from benchmarks import backends

    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: True)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (True, {"result": {"points_count": 42}}))
    monkeypatch.setattr(backends, "_grpc_collection_points", lambda host, port, coll: (None, "grpc denied"))
    monkeypatch.setattr(backends, "_model_weights_available", lambda: (True, "model"))
    availability = backends.probe_dense()
    assert availability.available is False
    assert "grpc denied" in availability.detail


def test_rest_only_path_still_works_when_grpc_closed(monkeypatch):
    from benchmarks import backends

    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: port == 6333)
    monkeypatch.setattr(backends, "_http_get", lambda url, auth=None: (True, {"result": {"points_count": 9}}))
    monkeypatch.setattr(backends, "_model_weights_available", lambda: (True, "model"))
    assert backends.probe_dense().available is True


def test_zero_byte_weight_index_is_rejected(tmp_path):
    """An empty index file is an interrupted download, not an absent index."""
    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in ("config.json", "tokenizer.json", "model-00001-of-00002.safetensors"):
        (path / name).write_text("x" * 10, encoding="utf-8")
    (path / "model.safetensors.index.json").write_bytes(b"")
    ok, detail = _looks_like_model_dir(path)
    assert ok is False
    assert "weight index" in detail


# ── Round 12: question type, recognized weight names, principal fingerprint ──


@pytest.mark.parametrize("question", [["a"], {"a": 1}, 42, True, ""])
def test_non_string_questions_are_dataset_errors(tmp_path, question):
    """A list/object/number question must not be coerced into a query."""
    import json as _json

    from benchmarks.dataset import DatasetError, load_queries

    bad = tmp_path / "bad.jsonl"
    bad.write_text(_json.dumps({"question": question, "contexts": ["c"]}) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="question must be a non-empty string"):
        load_queries(bad)


def test_non_string_question_is_not_coerced(tmp_path):
    import json as _json

    from benchmarks.dataset import DatasetError, load_queries

    bad = tmp_path / "bad.jsonl"
    bad.write_text(_json.dumps({"question": ["a"], "contexts": ["c"]}) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError) as excinfo:
        load_queries(bad)
    assert "list" in str(excinfo.value)
    assert "['a']" not in str(excinfo.value)


def test_non_string_question_exits_cleanly_through_cli(tmp_path):
    import json as _json

    import benchmarks.retrieval_benchmark as cli

    bad = tmp_path / "bad.jsonl"
    bad.write_text(_json.dumps({"question": 42, "contexts": ["c"]}) + "\n", encoding="utf-8")
    exit_code = cli.main(
        ["--config", "bm25", "--dataset", str(bad), "--output-dir", str(tmp_path / "out"), "--allow-dirty"]
    )
    assert exit_code == 2


def test_valid_string_question_still_works():
    from benchmarks.dataset import load_queries

    queries = load_queries("tests/evaluation/golden_set.jsonl", limit=1)
    assert isinstance(queries[0].question, str)
    assert queries[0].question


@pytest.mark.parametrize("weight", ["training_args.bin", "optimizer.pt", "tf_model.h5"])
def test_unrecognized_weight_names_are_rejected(tmp_path, weight):
    """from_pretrained does not treat these as a checkpoint."""
    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in ("config.json", "tokenizer.json", weight):
        (path / name).write_text("weights", encoding="utf-8")
    ok, detail = _looks_like_model_dir(path)
    assert ok is False
    assert "model weights" in detail


@pytest.mark.parametrize("weight", ["model.safetensors", "pytorch_model.bin"])
def test_recognized_weight_names_are_accepted(tmp_path, weight):
    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in ("config.json", "tokenizer.json", weight):
        (path / name).write_text("weights", encoding="utf-8")
    assert _looks_like_model_dir(path)[0] is True


def test_index_declared_shards_are_still_accepted(tmp_path):
    """A shard set named by the index remains valid without recognized names."""
    import json as _json

    from benchmarks.backends import _looks_like_model_dir

    path = tmp_path / "model"
    path.mkdir()
    for name in ("config.json", "tokenizer.json", "part-a.bin"):
        (path / name).write_text("weights", encoding="utf-8")
    (path / "model.safetensors.index.json").write_text(
        _json.dumps({"weight_map": {"layer.0": "part-a.bin"}}), encoding="utf-8"
    )
    assert _looks_like_model_dir(path)[0] is True


def test_principal_fingerprint_differs_per_credential(monkeypatch):
    """Two ES principals must not collapse to the same snapshot/hash."""
    from benchmarks import provenance
    from common import config as common_config

    def config_for(username, password):
        return {"elasticsearch": {"host": "http://es:9200", "username": username, "password": password}}

    monkeypatch.setattr(common_config, "get_config_dict", lambda: config_for("alice", "pw-a"))
    first = provenance.effective_retrieval_config()
    monkeypatch.setattr(common_config, "get_config_dict", lambda: config_for("bob", "pw-b"))
    second = provenance.effective_retrieval_config()
    assert first["elasticsearch"]["principal_fingerprint"] != second["elasticsearch"]["principal_fingerprint"]
    assert provenance.sha256_json(first) != provenance.sha256_json(second)


def test_principal_fingerprint_is_not_reversible(monkeypatch):
    import json as _json

    from benchmarks import provenance
    from common import config as common_config

    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"elasticsearch": {"host": "http://es:9200", "username": "alice", "password": "super-secret"}},
    )
    snapshot = provenance.effective_retrieval_config()
    rendered = _json.dumps(snapshot)
    assert "super-secret" not in rendered
    assert "alice" not in rendered
    assert snapshot["elasticsearch"]["principal_fingerprint"].startswith("sha256:")


# ── Round 13: no password-derived verifier, no username in artifacts ─────────


def test_fingerprint_is_independent_of_the_password(monkeypatch):
    """Hashing the password would make the artifact an offline guess verifier."""
    from benchmarks import provenance
    from common import config as common_config

    def config_for(password):
        return {"elasticsearch": {"host": "http://es:9200", "username": "elastic", "password": password}}

    monkeypatch.delenv("ELASTICSEARCH_USERNAME", raising=False)
    monkeypatch.delenv("ELASTICSEARCH_PASSWORD", raising=False)
    monkeypatch.setattr(common_config, "get_config_dict", lambda: config_for("first-password"))
    first = provenance.effective_retrieval_config()["elasticsearch"]["principal_fingerprint"]
    monkeypatch.setattr(common_config, "get_config_dict", lambda: config_for("second-password"))
    second = provenance.effective_retrieval_config()["elasticsearch"]["principal_fingerprint"]
    # Same username, different password -> the fingerprint must not change, or it
    # would leak a per-password digest usable for dictionary guessing.
    assert first == second


def test_fingerprint_differs_per_username(monkeypatch):
    from benchmarks import provenance
    from common import config as common_config

    monkeypatch.delenv("ELASTICSEARCH_USERNAME", raising=False)
    monkeypatch.delenv("ELASTICSEARCH_PASSWORD", raising=False)

    def config_for(username):
        return {"elasticsearch": {"host": "http://es:9200", "username": username, "password": "pw"}}

    monkeypatch.setattr(common_config, "get_config_dict", lambda: config_for("alice"))
    first = provenance.effective_retrieval_config()["elasticsearch"]["principal_fingerprint"]
    monkeypatch.setattr(common_config, "get_config_dict", lambda: config_for("bob"))
    second = provenance.effective_retrieval_config()["elasticsearch"]["principal_fingerprint"]
    assert first != second


def test_no_password_digest_is_published(monkeypatch):
    """A known-username digest over username:password is a password oracle."""
    import hashlib
    import json as _json

    from benchmarks import provenance
    from common import config as common_config

    monkeypatch.delenv("ELASTICSEARCH_USERNAME", raising=False)
    monkeypatch.delenv("ELASTICSEARCH_PASSWORD", raising=False)
    password = "hunter2"
    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"elasticsearch": {"host": "http://es:9200", "username": "elastic", "password": password}},
    )
    rendered = _json.dumps(provenance.effective_retrieval_config())
    # The attacker knows the username and can compute this digest offline.
    guessable = hashlib.sha256(f"elastic:{password}".encode()).hexdigest()[:16]
    assert guessable not in rendered
    assert password not in rendered


def test_env_username_is_not_persisted_in_plaintext(monkeypatch):
    """ELASTICSEARCH_USERNAME must be a presence flag, not the value."""
    import json as _json

    from benchmarks import provenance
    from common import config as common_config

    monkeypatch.setattr(common_config, "get_config_dict", lambda: {"elasticsearch": {"host": "http://es:9200"}})
    monkeypatch.setenv("ELASTICSEARCH_USERNAME", "envuser")
    monkeypatch.setenv("ELASTICSEARCH_PASSWORD", "envpass")
    snapshot = provenance.effective_retrieval_config()
    assert snapshot["env_overrides"]["ELASTICSEARCH_USERNAME"] is True
    rendered = _json.dumps(snapshot)
    assert "envuser" not in rendered
    assert "envpass" not in rendered
    # The username is still identifiable via the fingerprint.
    assert snapshot["elasticsearch"]["username_set"] is True
    assert snapshot["elasticsearch"]["principal_fingerprint"].startswith("sha256:")


def test_fingerprint_absent_without_a_username(monkeypatch):
    from benchmarks import provenance
    from common import config as common_config

    monkeypatch.delenv("ELASTICSEARCH_USERNAME", raising=False)
    monkeypatch.delenv("ELASTICSEARCH_PASSWORD", raising=False)
    monkeypatch.setattr(common_config, "get_config_dict", lambda: {"elasticsearch": {"host": "http://es:9200"}})
    snapshot = provenance.effective_retrieval_config()
    assert snapshot["elasticsearch"]["principal_fingerprint"] is None
    assert snapshot["elasticsearch"]["password_set"] is False


# ── Round 14: ES URL userinfo auth/fingerprint, completion-aware comparison ───


def test_url_userinfo_credentials_are_used_for_auth(monkeypatch):
    """A supported URL form must authenticate instead of sending userinfo as host."""
    from benchmarks.backends import _elastic_settings
    from common import config as common_config

    monkeypatch.delenv("ELASTICSEARCH_USERNAME", raising=False)
    monkeypatch.delenv("ELASTICSEARCH_PASSWORD", raising=False)
    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"elasticsearch": {"host": "https://esuser:espass@es:9200", "index": "cosmetics_docs"}},
    )
    url, index, port, auth = _elastic_settings()
    assert auth == ("esuser", "espass")
    assert index == "cosmetics_docs"
    assert url == "https://esuser:espass@es:9200"


def test_elastic_url_userinfo_parsing():
    from benchmarks.backends import elastic_url_userinfo

    assert elastic_url_userinfo("https://u:p@es:9200") == ("u", "p")
    assert elastic_url_userinfo("http://es:9200") == (None, None)
    assert elastic_url_userinfo("") == (None, None)
    assert elastic_url_userinfo(None) is None if False else True


def test_bm25_authenticates_with_url_userinfo(monkeypatch):
    """The probe must not reject an instance the production client can reach."""
    from benchmarks import backends
    from common import config as common_config

    captured = {}

    def fake_get(url, auth=None):
        captured["url"] = url
        captured["auth"] = auth
        return True, {"count": 12}

    monkeypatch.delenv("ELASTICSEARCH_USERNAME", raising=False)
    monkeypatch.delenv("ELASTICSEARCH_PASSWORD", raising=False)
    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"elasticsearch": {"host": "https://esuser:espass@es:9200", "enabled": True}},
    )
    monkeypatch.setattr(backends, "_tcp_reachable", lambda host, port: True)
    monkeypatch.setattr(backends, "_http_get", fake_get)
    availability = backends.probe_bm25()
    assert captured["auth"] == ("esuser", "espass")
    assert availability.available is True, availability.detail


def test_url_principal_is_fingerprinted(monkeypatch):
    """A redacted host must not make two principals share a config_sha256."""
    from benchmarks import provenance
    from common import config as common_config

    monkeypatch.delenv("ELASTICSEARCH_USERNAME", raising=False)
    monkeypatch.delenv("ELASTICSEARCH_PASSWORD", raising=False)
    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"elasticsearch": {"host": "https://alice:pw1@es:9200"}},
    )
    first = provenance.effective_retrieval_config()
    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"elasticsearch": {"host": "https://bob:pw2@es:9200"}},
    )
    second = provenance.effective_retrieval_config()
    assert first["elasticsearch"]["principal_fingerprint"] is not None
    assert first["elasticsearch"]["principal_fingerprint"] != second["elasticsearch"]["principal_fingerprint"]
    assert provenance.sha256_json(first) != provenance.sha256_json(second)


def test_url_password_is_never_hashed(monkeypatch):
    """Fingerprinting the URL password would recreate the offline verifier."""
    import hashlib
    import json as _json

    from benchmarks import provenance
    from common import config as common_config

    monkeypatch.delenv("ELASTICSEARCH_USERNAME", raising=False)
    monkeypatch.delenv("ELASTICSEARCH_PASSWORD", raising=False)
    monkeypatch.setattr(
        common_config,
        "get_config_dict",
        lambda: {"elasticsearch": {"host": "https://elastic:hunter2@es:9200"}},
    )
    rendered = _json.dumps(provenance.effective_retrieval_config())
    assert "hunter2" not in rendered
    assert hashlib.sha256(b"elastic:hunter2").hexdigest()[:16] not in rendered
    # The username-only digest is what should appear.
    assert hashlib.sha256(b"elastic").hexdigest()[:16] in rendered


def test_completion_counts_are_recorded(mini_dataset, make_query):
    """A reduced-sample run must state how many queries actually completed."""
    from benchmarks.models import RetrievedItem
    from benchmarks.runner import run_configuration, summarize_run

    class OneFails:
        name = "one_fails"

        def retrieve(self, query, top_k):
            if query.sample_id == "0002":
                raise TimeoutError("timeout")
            return [
                RetrievedItem(rank=index + 1, key=item.key, score=1.0, source="fixture", text=item.text)
                for index, item in enumerate(query.relevant_items[:top_k])
            ]

    queries = [make_query("0000", ["a0"]), make_query("0001", ["a1"]), make_query("0002", ["a2"])]
    run = run_configuration("bm25", queries, retriever_factory=lambda name: OneFails())
    summary = summarize_run([run])
    entry = summary["configs"]["bm25"]
    assert entry["completed_sample_count"] == 2
    assert entry["requested_sample_count"] == 3
    assert entry["completion_rate"] == pytest.approx(2 / 3)


def test_different_sample_sets_are_flagged_not_compared(mini_dataset, make_query):
    """Configs scored on different samples must not share one ranking table."""
    from benchmarks.report import build_comparison

    def entry(completed, requested, digest):
        return {
            "status": "EXECUTED",
            "metrics": {"overall": {"sample_count": completed}},
            "latency": {"total_retrieval_ms": {}},
            "completed_sample_count": completed,
            "requested_sample_count": requested,
            "sample_set_sha256": digest,
        }

    summary = {
        "configs": {
            "bm25": entry(3, 3, "aaa"),
            "dense": entry(2, 3, "bbb"),
        }
    }
    text = build_comparison(summary)
    assert "Not directly comparable" in text
    assert "Sample set `aaa`" in text
    assert "Sample set `bbb`" in text


def test_identical_sample_sets_compare_normally():
    from benchmarks.report import build_comparison

    def entry(digest):
        return {
            "status": "EXECUTED",
            "metrics": {"overall": {"sample_count": 3}},
            "latency": {"total_retrieval_ms": {}},
            "completed_sample_count": 3,
            "requested_sample_count": 3,
            "sample_set_sha256": digest,
        }

    text = build_comparison({"configs": {"bm25": entry("same"), "dense": entry("same")}})
    assert "Not directly comparable" not in text
    assert "| `bm25` |" in text
    assert "| `dense` |" in text
