"""Safety regressions for the real-corpus indexer, workpackage and binding check.

These tests pin the four properties this review round exists to guarantee:

* an existing non-empty index is never destroyed by default;
* experiment and production indexes are recognisably separated;
* external official documents are declared ``UNTRUSTED`` and cannot reach the
  production index without an explicit, content-bound approval;
* the review workpackage adds no label, reviewer, date or decision.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.corpus import index_real_corpus_v1_es as indexer
from scripts.corpus import verify_real_corpus_v1_binding as binding
from scripts.validation import build_real_golden_set_v1_workpackage as workpackage

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO_ROOT / "corpus" / "real_v1"
GOLDEN_DIR = REPO_ROOT / "tests" / "evaluation" / "golden_set_v1"


class _Indices:
    def __init__(self, store: dict) -> None:
        self._store = store

    def exists(self, index: str) -> bool:
        return index in self._store

    def create(self, index: str, mappings=None, settings=None) -> None:
        self._store[index] = {
            "mappings": copy.deepcopy(mappings) if mappings else {},
            "settings": copy.deepcopy(settings) if settings else {},
            "docs": {},
        }

    def delete(self, index: str) -> None:
        self._store.pop(index, None)

    def get_mapping(self, index: str) -> dict:
        return {index: {"mappings": self._store[index]["mappings"]}}

    def refresh(self, index: str) -> None:
        return None


class FakeIndexClient:
    """Enough Elasticsearch surface for the indexer and the binding verifier."""

    def __init__(self) -> None:
        self.store: dict = {}
        self.indices = _Indices(self.store)

    def info(self) -> dict:
        return {"version": {"number": "8.11.0"}}

    def index(self, index: str, id: str, document: dict) -> None:
        self.store[index]["docs"][id] = dict(document)

    def count(self, index: str, query: dict | None = None) -> dict:
        docs = self.store[index]["docs"].values()
        if query is None:
            return {"count": len(self.store[index]["docs"])}
        return {"count": sum(1 for document in docs if _matches(document, query))}

    def search(self, index: str, body: dict) -> dict:
        docs = list(self.store[index]["docs"].items())
        aggregations = body.get("aggs") or body.get("aggregations")
        if aggregations:
            return {"hits": {"hits": []}, "aggregations": _aggregate(docs, aggregations)}
        # fingerprint_elasticsearch: match_all with _source + search_after
        docs.sort(key=lambda item: item[1].get("chunk_id") or item[0])
        after = body.get("search_after")
        if after:
            docs = [item for item in docs if (item[1].get("chunk_id") or item[0]) > after[0]]
        page = docs[: body.get("size", 10)]
        hits = []
        for doc_id, document in page:
            hits.append(
                {
                    "_id": doc_id,
                    "_source": document,
                    "sort": [document.get("chunk_id") or doc_id],
                }
            )
        return {"hits": {"total": {"value": len(self.store[index]["docs"])}, "hits": hits}}


def _matches(document: dict, query: dict) -> bool:
    if "match_all" in query:
        return True
    if "term" in query:
        ((field, expected),) = query["term"].items()
        return document.get(field) == expected
    if "bool" in query:
        clauses = query["bool"]
        if any(not _matches(document, clause) for clause in clauses.get("must", [])):
            return False
        if any(_matches(document, clause) for clause in clauses.get("must_not", [])):
            return False
        should = clauses.get("should", [])
        if should:
            return sum(1 for clause in should if _matches(document, clause)) >= clauses.get("minimum_should_match", 1)
        return True
    raise NotImplementedError(query)


def _aggregate(docs, aggregations: dict) -> dict:
    result = {}
    for name, spec in aggregations.items():
        field = spec["terms"]["field"]
        counts: dict[str, int] = {}
        for _, document in docs:
            value = document
            for part in field.split("."):
                value = (value or {}).get(part) if isinstance(value, dict) else None
            if value is not None:
                counts[str(value)] = counts.get(str(value), 0) + 1
        result[name] = {"buckets": [{"key": key, "doc_count": count} for key, count in sorted(counts.items())]}
    return result


def _seed_index(client: FakeIndexClient, index: str, epoch: str, count: int = 2) -> None:
    from offline.elasticsearch_writer import ElasticsearchWriter

    writer = ElasticsearchWriter(client, index)
    writer.ensure_index()
    for position in range(count):
        writer.client.store[index]["docs"][f"doc-{position}"] = {
            "doc_id": f"doc-{position}",
            "chunk_id": f"chunk-{position}",
            "chunk_index": position,
            "content": f"existing content {position}",
            "doc_version_epoch": epoch,
            "status": "active",
            "role_mask": 0,
            "dept_mask": 0,
            "provenance": {"trust_class": "MANAGED_INTERNAL"},
        }


def _args(**overrides):
    defaults = {
        "chunks": str(CORPUS_DIR / "chunks.jsonl"),
        "sources_dir": str(CORPUS_DIR / "sources"),
        "out": str(REPO_ROOT / "artifacts" / "test-fingerprint.json"),
        "index": "cosmetics_docs",
        "host": "http://127.0.0.1:9200",
        "profile": "production",
        "recreate": False,
        "confirm_index": None,
        "append_epoch": False,
        "check_only": False,
        "staging_unapproved": False,
        "approval_store": ":memory:",
        "corpus_version": None,
        # Mirrors the CLI default: an empty string means "leave `content` on the
        # index default analyzer", not "no analyzer configured".
        "analyzer": "",
    }
    defaults.update(overrides)
    return type("Args", (), defaults)()


# ── 1. index rebuild is fail-closed ─────────────────────────────────────────


class TestIndexRebuildIsFailClosed:
    def test_non_empty_index_is_refused_without_an_explicit_action(self):
        state = indexer.IndexState(
            index_name="cosmetics_docs",
            exists=True,
            document_count=212,
            epochs=("real-v1-20261019",),
            trust_classes={"MANAGED_INTERNAL": 212},
            content_sha256="abc",
        )
        with pytest.raises(indexer.IndexSafetyError, match="refusing to write into existing non-empty index"):
            indexer.plan_index_write(state, profile="production", target_epoch="real-v1-20261019")

    def test_recreate_requires_the_exact_index_name(self):
        state = indexer.IndexState(index_name="cosmetics_docs", exists=True, document_count=10, epochs=("old",))
        with pytest.raises(indexer.IndexSafetyError, match="confirm-index"):
            indexer.plan_index_write(
                state, profile="production", target_epoch="new", recreate=True, confirm_index="wrong"
            )
        plan = indexer.plan_index_write(
            state,
            profile="production",
            target_epoch="new",
            recreate=True,
            confirm_index="cosmetics_docs",
        )
        assert plan.action == "recreate"

    def test_appending_an_epoch_that_already_exists_is_refused(self):
        state = indexer.IndexState(
            index_name="cosmetics_docs", exists=True, document_count=10, epochs=("real-v1-20261019",)
        )
        with pytest.raises(indexer.IndexSafetyError, match="already exists"):
            indexer.plan_index_write(
                state,
                profile="production",
                target_epoch="real-v1-20261019",
                append_epoch=True,
            )

    def test_an_empty_index_is_safe_to_write(self):
        state = indexer.IndexState(index_name="cosmetics_docs", exists=True, document_count=0)
        assert indexer.plan_index_write(state, profile="production", target_epoch="e").action == "index_empty"

    def test_check_only_reports_the_conflict_and_writes_nothing(self, tmp_path):
        client = FakeIndexClient()
        _seed_index(client, "cosmetics_docs", "old-epoch")
        before = copy.deepcopy(client.store)
        out = tmp_path / "fingerprint.json"
        code = indexer.run(
            _args(check_only=True, out=str(out)),
            client=client,
            registry=indexer.TrustRegistry(":memory:"),
        )
        assert code == 2
        assert out.exists() is False
        assert client.store == before, "preflight must not modify the index"

    def test_production_recreate_does_not_run_when_sources_are_unapproved(self):
        client = FakeIndexClient()
        _seed_index(client, "cosmetics_docs", "old-epoch")
        before = copy.deepcopy(client.store)
        with pytest.raises(indexer.IndexSafetyError, match="refusing to write the production index"):
            indexer.run(
                _args(recreate=True, confirm_index="cosmetics_docs"),
                client=client,
                registry=indexer.TrustRegistry(":memory:"),
            )
        assert client.store == before, "a refused write must leave the existing index intact"


# ── 2. profile isolation ────────────────────────────────────────────────────


class TestProfileIsolation:
    def test_production_profile_only_accepts_the_production_index(self):
        with pytest.raises(indexer.IndexSafetyError, match="production index"):
            indexer.resolve_profile("cosmetics_docs_exp_v1", "production")

    def test_experiment_profile_rejects_the_production_index(self):
        with pytest.raises(indexer.IndexSafetyError, match="experiment"):
            indexer.resolve_profile("cosmetics_docs", "experiment")

    def test_experiment_profile_requires_a_prefixed_name(self):
        with pytest.raises(indexer.IndexSafetyError, match="cosmetics_docs_exp_"):
            indexer.resolve_profile("random_index", "experiment")
        assert indexer.resolve_profile("cosmetics_docs_exp_v1", "experiment") == "experiment"

    def test_experiment_run_must_choose_its_own_fingerprint_path(self):
        client = FakeIndexClient()
        args = _args(
            profile="experiment",
            index="cosmetics_docs_exp_v1",
            staging_unapproved=True,
            out=str(CORPUS_DIR / "es_fingerprint.json"),
        )
        with pytest.raises(indexer.IndexSafetyError, match="must not write the committed production fingerprint"):
            indexer.run(args, client=client, registry=indexer.TrustRegistry(":memory:"))


# ── 3. source trust is not bypassed ─────────────────────────────────────────


class TestExternalSourcesStayUnreviewed:
    def test_source_records_are_declared_untrusted_and_pending(self):
        records = indexer.load_source_files(CORPUS_DIR / "sources")
        resolved, provenance = indexer.resolve_provenance(
            records,
            indexer.load_chunks(CORPUS_DIR / "chunks.jsonl"),
            registry=indexer.TrustRegistry(":memory:"),
        )
        assert set(provenance) == {
            "corpus/real_v1/sources/regulation_727.jsonl",
            "corpus/real_v1/sources/used_ingredients_2021.jsonl",
        }
        for payload in provenance.values():
            assert payload["source_trust"] == "UNTRUSTED"
            assert payload["approval_status"] == "PENDING_REVIEW"
            assert payload["trust_class"] == "UNTRUSTED"
            assert payload["approval_actor"] == ""
        assert {item["provenance"]["trust_class"] for item in resolved} == {"UNTRUSTED"}
        assert len(indexer.pending_sources(provenance)) == 2

    def test_the_corpus_builder_no_longer_claims_managed_internal(self):
        from scripts.corpus.build_real_corpus_v1 import _provenance

        payload = _provenance("nmpa/regulation-727/article-003")
        assert payload["source_trust"] == "UNTRUSTED"
        assert payload["approval_status"] == "PENDING_REVIEW"
        assert payload["trust_class"] == "UNTRUSTED"

    def test_a_production_write_with_unapproved_sources_is_refused(self):
        client = FakeIndexClient()
        with pytest.raises(indexer.IndexSafetyError, match="no approval bound to their reviewed bytes"):
            indexer.run(_args(), client=client, registry=indexer.TrustRegistry(":memory:"))
        assert "cosmetics_docs" not in client.store

    def test_an_approval_bound_to_other_bytes_does_not_unblock(self, tmp_path):
        registry = indexer.TrustRegistry(tmp_path / "approvals.sqlite3")
        registry.decide(
            "corpus/real_v1/sources/regulation_727.jsonl",
            content_hash="0" * 64,
            approval_status="APPROVED",
            actor="reviewer@example.org",
        )
        records = indexer.load_source_files(CORPUS_DIR / "sources")
        _, provenance = indexer.resolve_provenance(
            records, indexer.load_chunks(CORPUS_DIR / "chunks.jsonl"), registry=registry
        )
        assert provenance["corpus/real_v1/sources/regulation_727.jsonl"]["approval_status"] == "PENDING_REVIEW"
        assert len(indexer.pending_sources(provenance)) == 2

    def test_an_approval_bound_to_the_exact_file_unblocks_only_that_file(self, tmp_path):
        registry = indexer.TrustRegistry(tmp_path / "approvals.sqlite3")
        source_path = CORPUS_DIR / "sources" / "regulation_727.jsonl"
        registry.decide(
            "corpus/real_v1/sources/regulation_727.jsonl",
            content_hash=indexer.hashlib.sha256(source_path.read_bytes()).hexdigest(),
            approval_status="APPROVED",
            actor="reviewer@example.org",
        )
        records = indexer.load_source_files(CORPUS_DIR / "sources")
        _, provenance = indexer.resolve_provenance(
            records, indexer.load_chunks(CORPUS_DIR / "chunks.jsonl"), registry=registry
        )
        assert provenance["corpus/real_v1/sources/regulation_727.jsonl"]["approval_status"] == "APPROVED"
        assert provenance["corpus/real_v1/sources/used_ingredients_2021.jsonl"]["approval_status"] == "PENDING_REVIEW"
        assert indexer.pending_sources(provenance) == ["corpus/real_v1/sources/used_ingredients_2021.jsonl"]

    def test_an_experiment_index_stages_unapproved_sources_without_activating_them(self, tmp_path):
        client = FakeIndexClient()
        out = tmp_path / "experiment.json"
        code = indexer.run(
            _args(
                profile="experiment",
                index="cosmetics_docs_exp_v1",
                staging_unapproved=True,
                out=str(out),
            ),
            client=client,
            registry=indexer.TrustRegistry(":memory:"),
        )
        assert code == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["index_profile"] == "experiment"
        assert payload["source_trust_policy"]["trust_class_counts"] == {"UNTRUSTED": 2}
        assert payload["source_trust_policy"]["pending_source_count"] == 2
        assert payload["source_trust_policy"]["staging_unapproved"] is True
        assert "cosmetics_docs" not in client.store, "an experiment must not touch the production index"
        assert "cosmetics_docs_exp_v1" in client.store


# ── 3b. a declared analyzer reaches the cluster ──────────────────────────────


class TestDeclaredAnalyzerReachesTheCluster:
    """The tokenization A/B is only valid if the analyzer actually applied.

    Without this, ``--analyzer`` could be accepted and silently dropped, and the
    experiment would compare two default-analyzer indexes and report a
    real-looking null result. So the tests pin three separate things: the mapping
    the writer asks for, the mapping the cluster is read back as holding, and
    that it is the ``content`` field only.
    """

    def test_the_declared_analyzer_lands_on_the_content_field_only(self):
        from offline.elasticsearch_writer import ElasticsearchWriter

        client = FakeIndexClient()
        writer = ElasticsearchWriter(client, "cosmetics_docs_exp_ab", analyzer="smartcn")
        writer.ensure_index()
        properties = client.store["cosmetics_docs_exp_ab"]["mappings"]["properties"]

        assert properties["content"] == {"type": "text", "analyzer": "smartcn"}
        # Every other field keeps its declared type: a tokenization choice must
        # not be able to re-type the RBAC masks, the epoch or the ids.
        assert properties["doc_id"] == {"type": "keyword"}
        assert properties["role_mask"] == {"type": "long"}
        assert properties["doc_version_epoch"] == {"type": "keyword"}

    def test_the_applied_analyzer_is_read_back_from_the_cluster(self):
        from offline.elasticsearch_writer import ElasticsearchWriter

        client = FakeIndexClient()
        writer = ElasticsearchWriter(client, "cosmetics_docs_exp_ab", analyzer="smartcn")
        writer.ensure_index()
        assert writer.applied_analyzer() == "smartcn"

        # An index created without the option reports the cluster's own answer —
        # no analyzer on the field — rather than an assumption.
        default = ElasticsearchWriter(client, "cosmetics_docs_exp_default")
        default.ensure_index()
        assert default.applied_analyzer() == ""

    def test_the_writers_mapping_does_not_mutate_the_module_constant(self):
        from offline.elasticsearch_writer import INDEX_MAPPING, ElasticsearchWriter

        ElasticsearchWriter(FakeIndexClient(), "cosmetics_docs_exp_ab", analyzer="smartcn").mappings()
        # A shared module-level mapping mutated in place would silently re-type
        # every index built afterwards in the same process.
        assert INDEX_MAPPING["mappings"]["properties"]["content"] == {"type": "text"}

    def test_the_fingerprint_records_the_applied_analyzer_not_only_the_request(self, tmp_path):
        out = tmp_path / "ab.json"
        code = indexer.run(
            _args(
                profile="experiment",
                index="cosmetics_docs_exp_v3_ab",
                staging_unapproved=True,
                analyzer="smartcn",
                out=str(out),
            ),
            client=FakeIndexClient(),
            registry=indexer.TrustRegistry(":memory:"),
        )
        assert code == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["analyzer"]["requested"] == "smartcn"
        assert payload["analyzer"]["applied"] == "smartcn"
        assert payload["analyzer"]["field"] == "content"

    def test_a_preflight_states_only_what_it_can_know(self, tmp_path, capsys):
        """A preflight inspects an index that may not exist yet."""
        indexer.run(
            _args(profile="experiment", index="cosmetics_docs_exp_v3_ab", analyzer="smartcn", check_only=True),
            client=FakeIndexClient(),
            registry=indexer.TrustRegistry(":memory:"),
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["analyzer_requested"] == "smartcn"
        # Claiming an applied analyzer before the index exists would be asserting
        # a fact about a cluster state the run has not reached.
        assert "analyzer" not in payload


# ── 4. the review workpackage promotes nothing ──────────────────────────────


class TestWorkpackagePromotesNothing:
    def _records(self):
        candidates = [
            json.loads(line)
            for line in (GOLDEN_DIR / "candidates_v1.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        chunks = [
            json.loads(line)
            for line in (CORPUS_DIR / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        sources = workpackage._source_index(CORPUS_DIR / "sources")
        return workpackage.build_retrieval_records(candidates, chunks, sources)

    def test_all_27_candidates_are_present_and_still_drafts(self):
        records = self._records()
        assert [record["sample_id"] for record in records] == [f"REAL-V1-{index:03d}" for index in range(1, 28)]
        for record in records:
            assert record["review_status"] == "DRAFT_UNVERIFIED"
            assert record["scorable"] is False

    def test_decision_reviewer_date_and_notes_are_blank(self):
        for record in self._records():
            review = record["review"]
            assert review["decision"] == ""
            assert review["reviewer"] == ""
            assert review["reviewed_at"] == ""
            assert review["notes"] == ""
            assert review["relevance_changes"] == []
            assert review["added_passages"] == []
            assert review["removed_passages"] == []

    def test_each_row_carries_the_fields_a_reviewer_needs(self):
        required = {
            "sample_id",
            "question",
            "official_sources",
            "annotations",
            "derivation_risk",
            "possibly_missing_passages",
            "difficulty",
            "complexity_label",
            "review",
        }
        for record in self._records():
            assert required <= set(record)
            assert record["official_sources"]
            assert record["annotations"]
            for annotation in record["annotations"]:
                assert annotation["doc_id"] and annotation["chunk_id"] and annotation["text"]
                assert annotation["current_relevance"] in (0, 1, 2)

    def test_derivation_risk_is_deterministic_and_bounded(self):
        first = self._records()
        second = self._records()
        assert first == second
        for record in first:
            risk = record["derivation_risk"]
            assert risk["level"] in {"LOW", "MEDIUM", "HIGH"}
            assert 0.0 <= risk["lcs_ratio_of_question"] <= 1.0
            assert 0.0 <= risk["question_bigram_coverage"] <= 1.0

    def test_answer_gate_candidates_are_separate_and_excluded_from_recall(self):
        no_answer = [
            json.loads(line)
            for line in (GOLDEN_DIR / "no_answer_candidates_v1.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        chunks = [
            json.loads(line)
            for line in (CORPUS_DIR / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        records = workpackage.build_answer_gate_records(
            no_answer, chunks, workpackage._source_index(CORPUS_DIR / "sources")
        )
        assert len(records) == 3
        for record in records:
            assert record["excluded_from_retrieval_recall"] is True
            assert record["evaluation_target"] == "answer_gate"
            assert record["review_status"] == "DRAFT_UNVERIFIED"
            assert record["review"]["decision"] == ""
            assert record["review"]["reviewer"] == ""
            assert record["reviewer_questions"], "a reviewer must be prompted to judge semantics, not keywords"

    def test_a_substring_hit_is_not_presented_as_a_near_miss_for_the_substance(self):
        no_answer = [
            json.loads(line)
            for line in (GOLDEN_DIR / "no_answer_candidates_v1.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        chunks = [
            json.loads(line)
            for line in (CORPUS_DIR / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        records = workpackage.build_answer_gate_records(
            no_answer, chunks, workpackage._source_index(CORPUS_DIR / "sources")
        )
        methanol = next(record for record in records if "甲醇" in record["question"])
        assert methanol["near_miss_passages"], "the benzyl-alcohol row does contain the substring"
        for passage in methanol["near_miss_passages"]:
            assert passage["embedded_substring_terms"] == ["甲醇"]
            assert "longer substance name" in passage["why_not_an_answer"]

    def test_independent_proposals_are_not_in_the_dataset(self):
        records = workpackage.build_independent_records()
        assert records
        candidate_questions = {
            json.loads(line)["question"]
            for line in (GOLDEN_DIR / "candidates_v1.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        for record in records:
            assert record["in_dataset"] is False
            assert record["scorable"] is False
            assert record["annotations"] == []
            assert record["question"] not in candidate_questions


# ── 5. binding verification ─────────────────────────────────────────────────


class TestBindingVerification:
    def test_a_consistent_index_passes_every_check(self, tmp_path):
        client = FakeIndexClient()
        manifest = json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))
        chunks = [
            json.loads(line)
            for line in (CORPUS_DIR / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        from offline.elasticsearch_writer import ElasticsearchWriter

        writer = ElasticsearchWriter(client, "cosmetics_docs")
        writer.ensure_index()
        for chunk in chunks:
            client.store["cosmetics_docs"]["docs"][chunk["chunk_id"]] = {
                "doc_id": chunk["doc_id"],
                "chunk_id": chunk["chunk_id"],
                "content": chunk["text"],
                "doc_version_epoch": manifest["corpus_version"],
                "provenance": {"trust_class": "APPROVED_EXTERNAL"},
            }
        config_path = Path(tmp_path) / "config.json"
        config_path.write_text(json.dumps({"knowledge_version_epoch": manifest["corpus_version"]}), encoding="utf-8")
        args = type(
            "Args",
            (),
            {
                "corpus_dir": str(CORPUS_DIR),
                "candidates": str(GOLDEN_DIR / "candidates_v1.jsonl"),
                "index": "cosmetics_docs",
                "host": "http://127.0.0.1:9200",
                "config": str(config_path),
            },
        )()
        original = binding._client
        binding._client = lambda host: client
        try:
            code = binding.run(args)
        finally:
            binding._client = original
        assert code == 0

    def test_a_managed_internal_index_is_reported_as_not_rebuildable(self):
        client = FakeIndexClient()
        _seed_index(client, "cosmetics_docs", "real-v1-20261019")
        result = binding.check_trust_state(client, "cosmetics_docs")
        assert result["status"] == binding.STATUS_FAIL
        assert "MANAGED_INTERNAL" in json.dumps(result["detail"], ensure_ascii=False)
