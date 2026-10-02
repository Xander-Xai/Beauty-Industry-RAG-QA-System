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
    availability = backends.probe_dense()
    assert availability.available is True, availability.detail
    assert "6334" in availability.detail or "grpc" in availability.detail.lower()


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
