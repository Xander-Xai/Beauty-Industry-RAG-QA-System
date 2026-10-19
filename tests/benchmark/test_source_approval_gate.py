"""Source-approval verification tests for the retrieval benchmark.

The defect these pin
-------------------
``results_are_benchmark`` used to be ``any_results and attributable and not
synthetic``. Nothing in that conjunction asked where the indexed text came from,
so the run recorded in ``docs/validation/real-v1-bm25-run.md`` — PR #113 —
retrieved out of ``cosmetics_docs_exp_v2``, an index holding 82 documents from an
approved regulation and **130 from an unreviewed catalogue**, and still published
``results_are_benchmark: true``. Thirteen of the 27 gold samples were scored
against the unapproved half.

Every test here is about that: a real BM25 run, a real contract-valid signed
golden set, and one thing wrong with the corpus underneath. The suite is built
around a fake Elasticsearch client rather than a live cluster so the failure
modes are exactly reproducible, and the final test replays PR #113's index
shape end to end to prove the gate now refuses it.

The client double is deliberately literal about the two things a naive
implementation gets wrong: ``count()`` is compared against how many documents the
walk actually read (so a partial page cannot certify an index), and the ledger is
read through SQLite from a real file (so "the index says APPROVED" is never
enough on its own).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from benchmarks.source_approval import (
    gold_chunk_ids_from_rows,
    verify_index_source_approval,
)
from offline.source_trust import PROVENANCE_SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
REGULATION = "corpus/real_v1/sources/regulation_727.jsonl"
INGREDIENTS = "corpus/real_v1/sources/used_ingredients_2021.jsonl"
EPOCH = "real-v1-20261019"

#: The hash each source's approval is bound to. Real values, because a stale-hash
#: test that used a made-up hash would pass for the wrong reason.
REGULATION_HASH = "c9499ca5eefa368328a0ecbe57ea0410687fafcd32bbb5c9d0aa9014f6075c91"
INGREDIENTS_HASH = "3dd67722972d7a10dd3da56028bd85d649e68b17a87c0499f169252527c0c06b"


# ── Test doubles ─────────────────────────────────────────────────────────────


class FakeIndices:
    def __init__(self, names: set[str]) -> None:
        self._names = names

    def exists(self, *, index: str) -> bool:
        return index in self._names


class FakeES:
    """Enough of the Elasticsearch client for the whole-index walk.

    ``count`` and ``search`` are kept honest about one another: ``search`` serves
    ``size`` documents per page and honours ``search_after``, so a client whose
    declared count disagrees with its own pages produces the same
    ``index_read_incomplete`` failure a truncated real walk would.
    """

    def __init__(self, documents: list[dict], *, declared_count: int | None = None, missing: bool = False) -> None:
        self.documents = sorted(documents, key=lambda item: str(item.get("chunk_id") or ""))
        self.declared_count = len(self.documents) if declared_count is None else declared_count
        self.indices = FakeIndices(set() if missing else {"cosmetics_docs_test"})
        self.page_sizes: list[int] = []

    def count(self, *, index: str) -> dict:
        return {"count": self.declared_count}

    def search(self, *, index: str, body: dict) -> dict:
        after = body.get("search_after")
        start = 0
        if after:
            start = next(
                (i + 1 for i, doc in enumerate(self.documents) if str(doc.get("chunk_id")) == str(after[0])),
                len(self.documents),
            )
        size = int(body.get("size", 10))
        self.page_sizes.append(size)
        page = self.documents[start : start + size]
        return {
            "hits": {
                "hits": [{"_source": doc, "sort": [doc.get("chunk_id")]} for doc in page],
                "total": {"value": len(self.documents)},
            }
        }


def approved_provenance(source_id: str, content_hash: str, *, actor: str = "Xander-Xai", **overrides: Any) -> dict:
    """Provenance exactly as ``TrustRegistry.resolve`` persists an approval."""
    payload = {
        "source_id": source_id,
        "source_trust": "UNTRUSTED",
        "approval_status": "APPROVED",
        "approval_actor": actor,
        "approval_decided_at": "2026-10-19T15:45:47.887813+00:00",
        "approval_note": "owner approval of the reviewed bytes",
        "approved_content_hash": content_hash,
        "trust_class": "APPROVED_EXTERNAL",
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
    }
    payload.update(overrides)
    return payload


def untrusted_provenance(source_id: str) -> dict:
    return {
        "source_id": source_id,
        "source_trust": "UNTRUSTED",
        "approval_status": "PENDING_REVIEW",
        "approval_actor": "",
        "approval_decided_at": "",
        "approval_note": "",
        "approved_content_hash": "",
        "trust_class": "UNTRUSTED",
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
    }


def document(chunk_id: str, provenance: dict, *, epoch: str = EPOCH, content: str = "条款正文") -> dict:
    return {
        "chunk_id": chunk_id,
        "doc_id": f"doc-{chunk_id}",
        "doc_version_epoch": epoch,
        "content": content,
        "provenance": provenance,
    }


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    """A real SQLite ledger holding the two approvals this suite reasons about."""
    path = tmp_path / "offline_source_trust.sqlite3"
    connection = sqlite3.connect(path)
    with connection:
        connection.execute(
            """
            CREATE TABLE source_trust_decisions (
                source_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                approval_status TEXT NOT NULL,
                decided_by TEXT NOT NULL,
                decided_at TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        connection.executemany(
            "INSERT INTO source_trust_decisions VALUES (?, ?, 'APPROVED', 'Xander-Xai', '2026-10-19T15:45:47.887813+00:00', '', 1)",
            [(REGULATION, REGULATION_HASH), (INGREDIENTS, INGREDIENTS_HASH)],
        )
    connection.close()
    return path


@pytest.fixture
def corpus_root(tmp_path: Path) -> Path:
    """A repo root whose source files hash to the approved values.

    The verifier re-hashes the bytes a reviewer could have opened, so the test
    corpus has to contain files with those exact digests. Rewriting the bytes to
    hit a target hash is not possible, so the two source files are *copied* from
    the real corpus and the fixture asserts they still match — if the corpus
    changed, the tests fail loudly instead of silently drifting.
    """
    root = tmp_path / "repo"
    (root / "corpus/real_v1/sources").mkdir(parents=True)
    for relative, digest in ((REGULATION, REGULATION_HASH), (INGREDIENTS, INGREDIENTS_HASH)):
        source = REPO_ROOT / relative
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        assert actual == digest, (
            f"{relative} no longer hashes to the value these tests approve; the fixture and the "
            "recorded approval would otherwise disagree"
        )
        (root / relative).write_bytes(source.read_bytes())
    return root


def codes(report) -> dict[str, int]:
    return (report.as_dict()["issues"] or {}).get("by_code", {})


def verify(client, corpus_root: Path, ledger: Path, **kwargs: Any):
    kwargs.setdefault("expected_epoch", EPOCH)
    return verify_index_source_approval(
        client,
        index_name="cosmetics_docs_test",
        gold_chunk_ids=kwargs.pop("gold_chunk_ids", frozenset()),
        ledger_path=str(ledger),
        repo_root=corpus_root,
        **kwargs,
    )


def two_approved_documents() -> list[dict]:
    return [
        document("chunk-reg", approved_provenance(REGULATION, REGULATION_HASH)),
        document("chunk-ing", approved_provenance(INGREDIENTS, INGREDIENTS_HASH)),
    ]


# ── 1. the pass path ─────────────────────────────────────────────────────────


def test_two_of_two_approved_sources_verify(corpus_root: Path, ledger: Path):
    """The gate must be passable, or it is not a gate."""
    report = verify(FakeES(two_approved_documents()), corpus_root, ledger)

    assert report.verified is True, codes(report)
    assert report.document_count == 2
    assert report.documents_verified == 2
    assert report.source_count == 2
    assert report.trust_class_counts == {"APPROVED_EXTERNAL": 2}
    assert report.observed_epochs == (EPOCH,)
    assert report.ledger_reachable is True
    assert {entry["verdict"] for entry in report.sources} == {"approved"}
    # The evidence has to carry who approved what, or a reader cannot audit it.
    assert {entry["approved_content_hash"] for entry in report.sources} == {REGULATION_HASH, INGREDIENTS_HASH}
    assert {entry["approval_actor"] for entry in report.sources} == {"Xander-Xai"}


# ── 2. partial approval ──────────────────────────────────────────────────────


def test_one_of_two_sources_approved_does_not_verify(corpus_root: Path, ledger: Path):
    """Exactly the PR #113 state: one approval, one unreviewed catalogue."""
    documents = [
        document("chunk-reg", approved_provenance(REGULATION, REGULATION_HASH)),
        document("chunk-ing", untrusted_provenance(INGREDIENTS)),
    ]
    report = verify(FakeES(documents), corpus_root, ledger)

    assert report.verified is False
    # The approved half is still reported as approved; the verdict is per source.
    verdicts = {entry["source_id"]: entry["verdict"] for entry in report.sources}
    assert verdicts[REGULATION] == "approved"
    assert verdicts[INGREDIENTS] not in ("approved", "managed_internal_unverifiable")
    assert codes(report)["source_not_activatable"] == 1
    # The ledger *does* contain an approval for it, which is exactly why this is
    # the interesting case: the index was built before the approval existed.
    assert "source_not_in_ledger" not in codes(report)


def test_source_with_approval_in_the_index_but_not_in_the_ledger_is_refused(corpus_root: Path, ledger: Path):
    """An index asserting APPROVED proves nothing on its own."""
    documents = [document("chunk-x", approved_provenance("corpus/real_v1/sources/unknown.jsonl", "0" * 64))]
    report = verify(FakeES(documents), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["source_not_in_ledger"] == 1


# ── 3. stale approval ────────────────────────────────────────────────────────


def test_approval_bound_to_superseded_bytes_is_refused(corpus_root: Path, ledger: Path):
    """Editing an approved source returns it to PENDING_REVIEW, index or not.

    The index still carries the old hash and the ledger still carries the old
    decision; only the bytes on disk moved. Re-hashing the file is the only check
    that catches this, which is why the verifier reads the source rather than
    trusting either stored copy.
    """
    (corpus_root / REGULATION).write_text('{"tampered": true}\n', encoding="utf-8")
    report = verify(FakeES(two_approved_documents()), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["approved_bytes_changed_since_approval"] == 1
    verdict = next(entry for entry in report.sources if entry["source_id"] == REGULATION)
    assert verdict["verdict"] == "stale_approval"


def test_index_hash_disagreeing_with_the_ledger_is_refused(corpus_root: Path, ledger: Path):
    """The index claims a different approved hash than the ledger recorded."""
    documents = [document("chunk-reg", approved_provenance(REGULATION, "a" * 64))]
    report = verify(FakeES(documents), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["indexed_hash_disagrees_with_ledger"] == 1


# ── 4. unattributed approval ─────────────────────────────────────────────────


def test_missing_approval_actor_is_refused(corpus_root: Path, ledger: Path):
    """An approval nobody can attribute is indistinguishable from one nobody made."""
    documents = [document("chunk-reg", approved_provenance(REGULATION, REGULATION_HASH, actor=""))]
    report = verify(FakeES(documents), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["approval_actor_missing"] == 1


def test_actor_disagreeing_with_the_ledger_is_refused(corpus_root: Path, ledger: Path):
    """An index must not reattribute a decision the ledger attributes elsewhere."""
    documents = [document("chunk-reg", approved_provenance(REGULATION, REGULATION_HASH, actor="someone-else"))]
    report = verify(FakeES(documents), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["approval_actor_disagrees_with_ledger"] == 1


# ── 5. missing / laundered provenance ────────────────────────────────────────


def test_empty_trust_class_is_refused(corpus_root: Path, ledger: Path):
    """A payload that declares no activation state cannot be activated.

    ``trust_class`` is present but blank, which is what a payload written by a
    build that did not derive it looks like. Refusing it matters because the
    alternative — treating an empty value as unstated and inferring — is how an
    unreviewed document acquires a class it was never given.
    """
    provenance = approved_provenance(REGULATION, REGULATION_HASH, trust_class="")
    report = verify(FakeES([document("chunk-reg", provenance)]), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["trust_class_missing"] == 1


def test_absent_trust_class_field_is_refused_as_incomplete(corpus_root: Path, ledger: Path):
    """Absent and blank are different defects: one predates the schema check."""
    provenance = approved_provenance(REGULATION, REGULATION_HASH)
    del provenance["trust_class"]
    report = verify(FakeES([document("chunk-reg", provenance)]), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["provenance_incomplete"] == 1


def test_trust_class_inconsistent_with_its_own_axes_is_refused(corpus_root: Path, ledger: Path):
    """A payload cannot claim APPROVED_EXTERNAL while its axes say PENDING_REVIEW."""
    provenance = untrusted_provenance(REGULATION)
    provenance["trust_class"] = "APPROVED_EXTERNAL"
    report = verify(FakeES([document("chunk-reg", provenance)]), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["trust_class_inconsistent"] == 1


def test_document_without_provenance_is_refused(corpus_root: Path, ledger: Path):
    report = verify(FakeES([{"chunk_id": "chunk-x", "doc_id": "d", "doc_version_epoch": EPOCH}]), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["provenance_missing"] == 1


def test_partial_provenance_is_refused(corpus_root: Path, ledger: Path):
    provenance = approved_provenance(REGULATION, REGULATION_HASH)
    del provenance["approval_actor"]
    report = verify(FakeES([document("chunk-reg", provenance)]), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["provenance_incomplete"] == 1


def test_provenance_from_another_schema_version_is_refused(corpus_root: Path, ledger: Path):
    provenance = approved_provenance(REGULATION, REGULATION_HASH, provenance_schema_version="ingestion-source-trust/0")
    report = verify(FakeES([document("chunk-reg", provenance)]), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["provenance_schema_mismatch"] == 1


def test_managed_internal_claim_from_an_index_is_not_treated_as_reviewed(corpus_root: Path, ledger: Path):
    """An index cannot attest to its own provenance.

    ``cosmetics_docs`` declares 209 logical ids as ``MANAGED_INTERNAL`` with no
    approval and no content hash. That claim is unverifiable by construction —
    there is nothing to re-hash and nothing that could disagree — so it may not
    back a benchmark result, and it is reported with its own code so an operator
    is not sent to record a ledger row the trust contract would ignore.
    """
    provenance = {
        "source_id": "nmpa/regulation-727/article-001",
        "source_trust": "MANAGED_INTERNAL",
        "approval_status": "NOT_REQUIRED",
        "approval_actor": "",
        "approval_decided_at": "",
        "approval_note": "",
        "approved_content_hash": "",
        "trust_class": "MANAGED_INTERNAL",
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
    }
    report = verify(FakeES([document("chunk-reg", provenance)]), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["source_managed_internal_not_reviewable"] == 1
    assert report.sources[0]["verdict"] == "managed_internal_unverifiable"


# ── 6. epoch binding ─────────────────────────────────────────────────────────


def test_wrong_epoch_is_refused(corpus_root: Path, ledger: Path):
    """The configured retrieval filter would match none of these documents."""
    documents = [
        document("chunk-reg", approved_provenance(REGULATION, REGULATION_HASH), epoch="real-v1-20260101"),
    ]
    report = verify(FakeES(documents), corpus_root, ledger, expected_epoch=EPOCH)

    assert report.verified is False
    assert codes(report)["epoch_mismatch"] == 1


def test_mixed_epochs_in_one_index_are_refused(corpus_root: Path, ledger: Path):
    documents = [
        document("chunk-reg", approved_provenance(REGULATION, REGULATION_HASH), epoch=EPOCH),
        document("chunk-ing", approved_provenance(INGREDIENTS, INGREDIENTS_HASH), epoch="real-v1-20260101"),
    ]
    report = verify(FakeES(documents), corpus_root, ledger, expected_epoch=EPOCH)

    assert report.verified is False
    assert codes(report)["epoch_mismatch"] == 1


def test_unconfigured_epoch_is_refused(corpus_root: Path, ledger: Path):
    """Without an expected epoch the index cannot be bound to an intended corpus."""
    report = verify(FakeES(two_approved_documents()), corpus_root, ledger, expected_epoch="")

    assert report.verified is False
    assert codes(report)["epoch_not_configured"] == 1


# ── 7. staging ───────────────────────────────────────────────────────────────


def test_staging_unapproved_documents_are_refused(corpus_root: Path, ledger: Path):
    """A document the indexer explicitly marked staged may never be activated."""
    provenance = approved_provenance(REGULATION, REGULATION_HASH)
    provenance["staging_unapproved"] = True
    report = verify(FakeES([document("chunk-reg", provenance)]), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["staging_unapproved"] == 1


def test_index_built_with_staging_unapproved_is_refused(corpus_root: Path, ledger: Path):
    """Even a fully-approved index built through the staging path is refused.

    The documents carry no staging flag of their own once the flag lives in the
    fingerprint payload, so the flag has to be passed down or the gate would miss
    the one index that was knowingly built as a non-activatable snapshot.
    """
    report = verify(FakeES(two_approved_documents()), corpus_root, ledger, staging_unapproved=True)

    assert report.verified is False
    assert codes(report)["index_built_with_staging_unapproved"] == 1


# ── 8. mixed indexes ─────────────────────────────────────────────────────────


def test_mixed_approved_and_unapproved_index_is_refused(corpus_root: Path, ledger: Path):
    """PR #113's exact shape: an approved regulation beside an unreviewed catalogue."""
    documents = [document(f"chunk-reg-{n}", approved_provenance(REGULATION, REGULATION_HASH)) for n in range(82)] + [
        document(f"chunk-ing-{n}", untrusted_provenance(INGREDIENTS)) for n in range(130)
    ]
    report = verify(FakeES(documents), corpus_root, ledger)

    assert report.verified is False
    assert report.document_count == 212
    assert codes(report)["source_not_activatable"] == 130
    # The approved half must not be laundered into an overall pass.
    assert report.trust_class_counts == {"APPROVED_EXTERNAL": 82}


# ── 9. whole-index coverage ──────────────────────────────────────────────────


def test_a_partial_walk_cannot_certify_an_index(corpus_root: Path, ledger: Path):
    """Checking the top-N rows would sample the answers, not the corpus."""
    documents = [document(f"chunk-{n}", approved_provenance(REGULATION, REGULATION_HASH)) for n in range(10)]
    report = verify(FakeES(documents, declared_count=212), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["index_read_incomplete"] == 1
    assert report.document_count == 212
    assert report.documents_verified == 10


def test_a_paged_index_is_verified_in_full(corpus_root: Path, ledger: Path):
    """A corpus larger than one page must still verify — paging is not sampling."""
    documents = [document(f"chunk-{n:04d}", approved_provenance(REGULATION, REGULATION_HASH)) for n in range(1200)]
    client = FakeES(documents)
    report = verify(client, corpus_root, ledger)

    assert report.verified is True, codes(report)
    assert report.documents_verified == 1200
    assert len(client.page_sizes) > 1, "the walk must actually page rather than request everything at once"


# ── 10. gold chunks ──────────────────────────────────────────────────────────


def test_gold_chunks_must_resolve_in_the_index(corpus_root: Path, ledger: Path):
    """A gold chunk that is not in the index cannot be answered by it."""
    report = verify(
        FakeES(two_approved_documents()),
        corpus_root,
        ledger,
        gold_chunk_ids=frozenset({"chunk-reg", "chunk-ing", "chunk-missing"}),
    )

    assert report.verified is False
    assert report.gold_chunk_total == 3
    assert report.gold_chunk_resolved == 2
    assert report.gold_chunk_unresolved == ("chunk-missing",)
    assert codes(report)["gold_chunk_unresolved"] == 1


def test_gold_chunk_from_an_unapproved_source_is_refused(corpus_root: Path, ledger: Path):
    """The chunk resolves, but its source did not pass review."""
    documents = [
        document("chunk-reg", approved_provenance(REGULATION, REGULATION_HASH)),
        document("chunk-ing", untrusted_provenance(INGREDIENTS)),
    ]
    report = verify(
        FakeES(documents),
        corpus_root,
        ledger,
        gold_chunk_ids=frozenset({"chunk-reg", "chunk-ing"}),
    )

    assert report.verified is False
    assert report.gold_chunk_unresolved == ()
    assert report.gold_chunks_from_unapproved_sources == ("chunk-ing",)
    assert codes(report)["gold_chunk_from_unapproved_source"] == 1


def test_gold_chunk_ids_are_read_from_annotations():
    rows = [
        {"annotations": [{"chunk_id": "a"}, {"chunk_id": "b"}]},
        {"annotations": [{"chunk_id": "a"}]},
        {"annotations": []},
        {"contexts": ["no annotations at all"]},
        "not even a mapping",
    ]
    assert gold_chunk_ids_from_rows(rows) == frozenset({"a", "b"})


# ── 11. unavailable evidence ─────────────────────────────────────────────────


def test_unreachable_ledger_is_refused(corpus_root: Path, tmp_path: Path):
    """No ledger means no cross-check, and no cross-check means no publication."""
    report = verify(FakeES(two_approved_documents()), corpus_root, tmp_path / "absent.sqlite3")

    assert report.verified is False
    assert report.ledger_reachable is False
    assert codes(report)["approval_ledger_unavailable"] == 1


def test_missing_index_is_refused(corpus_root: Path, ledger: Path):
    report = verify(FakeES(two_approved_documents(), missing=True), corpus_root, ledger)

    assert report.verified is False
    assert report.issues["by_code"] == {"index_missing": 1}


def test_revoked_approval_is_refused(corpus_root: Path, ledger: Path, tmp_path: Path):
    """A source a reviewer rejected may not still be indexed."""
    connection = sqlite3.connect(ledger)
    with connection:
        connection.execute(
            "UPDATE source_trust_decisions SET approval_status = 'REJECTED' WHERE source_id = ?",
            (INGREDIENTS,),
        )
    connection.close()

    report = verify(FakeES(two_approved_documents()), corpus_root, ledger)

    assert report.verified is False
    assert codes(report)["source_approval_revoked"] == 1


# ── 12. the publication gate itself ──────────────────────────────────────────


def _write_golden_set(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return path


def _contract_row(sample_id: str, doc_id: str, chunk_id: str, text: str) -> dict[str, Any]:
    """A row satisfying every clause of ``golden-set-contract/v2``."""
    return {
        "sample_id": sample_id,
        "question": f"问题 {sample_id}",
        "contexts": [text],
        "business_type": "regulation",
        "difficulty": "easy",
        "visual_required": False,
        "complexity_label": "simple",
        "corpus_version": EPOCH,
        "corpus_sha256": REGULATION_HASH,
        "annotations": [{"doc_id": doc_id, "chunk_id": chunk_id, "text": text}],
        "annotation": {
            "annotator": "llm:draft",
            "method": "llm-assisted-passage-mapping",
            "annotated_at": "2026-10-19",
            "review_status": "REVIEWED",
            "source": "llm_candidate",
            "reviewed_by": "Xander-Xai",
            "promoted_by": "Xander-Xai",
        },
    }


class ContractFixtureRetriever:
    """Deterministic retriever keyed by stable identifier, like a real one.

    Returns ``(doc_id, chunk_id)`` payloads so ground truth matches by identity
    rather than by normalized text. It carries no ``is_production`` marker, so the
    runner labels it synthetic — which is exactly what the CLI tests below need
    in order to isolate the source-approval operand from the retriever one.
    """

    name = "contract-fixture"

    def __init__(self, keys: dict[str, tuple[str, str]], texts: dict[str, list[str]]) -> None:
        self._keys = keys
        self._texts = texts

    def retrieve(self, query, top_k):
        from benchmarks.models import RetrievedItem
        from benchmarks.relevance import relevance_key

        doc_id, chunk_id = self._keys.get(query.sample_id, ("", ""))
        items = []
        for rank, text in enumerate(self._texts.get(query.sample_id, [])[:top_k], start=1):
            items.append(
                RetrievedItem(
                    rank=rank,
                    key=relevance_key({"doc_id": doc_id, "chunk_id": chunk_id}),
                    score=1.0 / rank,
                    source="contract-fixture",
                    text=text,
                )
            )
        return items


@pytest.fixture
def cli_with_retriever(monkeypatch):
    """CLI under test, with execution stubbed but every gate left real.

    Only ``run_configuration`` is replaced, so the dataset-contract gate, the
    source-approval gate and the publication decision all run for real against a
    supplied corpus. The retriever is a fixture on purpose — see
    :class:`ContractFixtureRetriever` — which is why every test using it can
    still assert precisely which operand blocked publication.
    """
    from benchmarks import retrieval_benchmark as cli

    monkeypatch.setattr(cli, "git_provenance", lambda root: ("deadbeef", False))
    original = cli.run_configuration

    def _with_retriever(name, queries, top_k=10, **_kwargs):
        keys = {
            q.sample_id: (
                q.raw.get("annotations", [{}])[0].get("doc_id", ""),
                q.raw.get("annotations", [{}])[0].get("chunk_id", ""),
            )
            for q in queries
        }
        texts = {q.sample_id: [a.get("text", "") for a in q.raw.get("annotations", [])] for q in queries}
        return original(
            name, queries, top_k=top_k, retriever_factory=lambda _cfg: ContractFixtureRetriever(keys, texts)
        )

    monkeypatch.setattr(cli, "run_configuration", _with_retriever)
    return cli


def _stub_approval(monkeypatch, report_payload: dict[str, Any]) -> None:
    """Feed a prepared source-approval verdict to the CLI.

    Patched on the CLI module, not on ``benchmarks.source_approval``: the CLI
    imports the name directly, so rebinding it there would leave the CLI
    calling the real Elasticsearch client.
    """
    from benchmarks import retrieval_benchmark as cli

    monkeypatch.setattr(
        cli,
        "gather_source_approval_evidence",
        lambda **_kwargs: json.loads(json.dumps(report_payload)),
    )


def test_pr113_mixed_index_result_is_not_publishable(tmp_path, corpus_root, ledger, monkeypatch, cli_with_retriever):
    """The regression this whole gate exists for.

    Reproduces the shape of the run recorded in ``docs/validation/real-v1-bm25-run.md``:
    a contract-valid, human-signed golden set, a real BM25 configuration that
    actually executed and produced metrics — over an index whose second source was
    never approved. That run published. This one must not, and the refusal has to
    be attributable to the corpus rather than to the dataset or the ranking.
    """
    documents = [document(f"chunk-reg-{n}", approved_provenance(REGULATION, REGULATION_HASH)) for n in range(82)] + [
        document(f"chunk-ing-{n}", untrusted_provenance(INGREDIENTS)) for n in range(130)
    ]
    _stub_approval(
        monkeypatch,
        verify(
            FakeES(documents),
            corpus_root,
            ledger,
            gold_chunk_ids=frozenset({f"chunk-reg-{n}" for n in range(3)} | {f"chunk-ing-{n}" for n in range(2)}),
        ).as_dict(),
    )

    dataset = _write_golden_set(
        tmp_path / "golden.jsonl",
        [
            _contract_row("REAL-V1-001", "doc-chunk-reg-0", "chunk-reg-0", "条款正文"),
            _contract_row("REAL-V1-010", "doc-chunk-ing-0", "chunk-ing-0", "目录正文"),
            _contract_row("REAL-V1-020", "doc-chunk-ing-1", "chunk-ing-1", "目录正文二"),
        ],
    )
    out = tmp_path / "out"

    exit_code = cli_with_retriever.main(
        [
            "--config",
            "bm25",
            "--dataset",
            str(dataset),
            "--output-dir",
            str(out),
            "--require-contract",
            "--require-integrity",
            "--require-results",
        ]
    )

    run_dir = sorted(p for p in out.iterdir() if p.is_dir())[-1]
    metadata = json.loads((run_dir / "metadata.json").read_text("utf-8"))
    summary = json.loads((run_dir / "retrieval_metrics.json").read_text("utf-8"))

    # Everything except source approval is satisfied — retrieval really ran and
    # really produced metrics, which is what made the original run convincing.
    assert exit_code == 0
    assert summary["any_results"] is True
    assert summary["configs"]["bm25"]["metrics"]["overall"]["sample_count"] == 3
    assert metadata["dataset_contract"]["valid_count"] == 3
    assert metadata["attributable"] is True
    assert metadata["dataset_integrity_errors"] == []

    # The corpus is what refuses it.
    assert metadata["source_approval_verified"] is False
    assert metadata["provenance_verified"] is False
    assert metadata["results_are_benchmark"] is False
    assert metadata["publishable"] is False
    assert metadata["source_approval"]["issues"]["by_code"]["source_not_activatable"] == 130
    assert metadata["source_approval"]["gold_chunks_from_unapproved_sources"] == ["chunk-ing-0", "chunk-ing-1"]
    assert any("source_approval_not_verified" in blocker for blocker in metadata["publication_blockers"])

    # And the human-readable report says so, not just the machine field.
    report_text = (run_dir / "report.md").read_text("utf-8")
    assert "Not publishable as a benchmark result" in report_text
    assert "Source approval was not verified" in report_text


def test_require_source_approval_exits_non_zero_on_an_unapproved_index(
    tmp_path, corpus_root, ledger, monkeypatch, capsys, cli_with_retriever
):
    """The strict flag turns the artifact field into a process exit code."""
    _stub_approval(
        monkeypatch,
        verify(FakeES([document("chunk-reg", untrusted_provenance(REGULATION))]), corpus_root, ledger).as_dict(),
    )
    dataset = _write_golden_set(tmp_path / "golden.jsonl", [_contract_row("REAL-V1-001", "d", "chunk-reg", "条款正文")])

    exit_code = cli_with_retriever.main(
        [
            "--config",
            "bm25",
            "--dataset",
            str(dataset),
            "--output-dir",
            str(tmp_path / "out"),
            "--require-source-approval",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 6
    assert "Source approval not verified" in captured.err
    assert "source_not_activatable" in captured.err


def test_a_fully_approved_clears_every_gate_the_cli_controls(
    tmp_path, corpus_root, ledger, monkeypatch, cli_with_retriever
):
    """The pass path: source approval stops being a blocker.

    ``synthetic_retriever`` stays true — the marker is read from the executor's
    own ``is_production`` declaration, which no CLI test can supply without a
    live cluster. So this asserts the exact decomposition rather than faking the
    last operand: the dataset clears the contract, retrieval really executed, and
    the *only* thing left on the blocker list is the retriever's honesty marker.
    That is what a clean run looks like from inside the gate.
    """
    _stub_approval(
        monkeypatch,
        verify(
            FakeES(two_approved_documents()),
            corpus_root,
            ledger,
            gold_chunk_ids=frozenset({"chunk-reg", "chunk-ing"}),
        ).as_dict(),
    )
    dataset = _write_golden_set(
        tmp_path / "golden.jsonl",
        [
            _contract_row("REAL-V1-001", "doc-chunk-reg", "chunk-reg", "条款正文"),
            _contract_row("REAL-V1-010", "doc-chunk-ing", "chunk-ing", "目录正文"),
        ],
    )
    out = tmp_path / "out"

    exit_code = cli_with_retriever.main(
        ["--config", "bm25", "--dataset", str(dataset), "--output-dir", str(out), "--require-source-approval"]
    )
    run_dir = sorted(p for p in out.iterdir() if p.is_dir())[-1]
    metadata = json.loads((run_dir / "metadata.json").read_text("utf-8"))
    summary = json.loads((run_dir / "retrieval_metrics.json").read_text("utf-8"))

    assert exit_code == 0, "--require-source-approval must not fail a clean corpus"
    assert summary["any_results"] is True
    assert metadata["attributable"] is True
    assert metadata["source_approval_verified"] is True
    assert metadata["provenance_verified"] is True
    assert metadata["source_approval"]["documents_verified"] == 2
    assert metadata["source_approval"]["gold_chunk_resolved"] == 2
    assert metadata["publication_blockers"] == ["synthetic_retriever"]
    assert metadata["results_are_benchmark"] is False  # only because the retriever is a fixture


def test_a_fixture_retriever_cannot_buy_publication_from_a_clean_index(
    tmp_path, corpus_root, ledger, monkeypatch, cli_with_retriever
):
    """The two operands are independent: a perfect corpus does not excuse a fixture."""
    _stub_approval(
        monkeypatch,
        verify(
            FakeES(two_approved_documents()), corpus_root, ledger, gold_chunk_ids=frozenset({"chunk-reg"})
        ).as_dict(),
    )
    dataset = _write_golden_set(tmp_path / "golden.jsonl", [_contract_row("REAL-V1-001", "d", "chunk-reg", "条款正文")])
    out = tmp_path / "out"

    cli_with_retriever.main(["--config", "bm25", "--dataset", str(dataset), "--output-dir", str(out)])
    run_dir = sorted(p for p in out.iterdir() if p.is_dir())[-1]
    metadata = json.loads((run_dir / "metadata.json").read_text("utf-8"))

    assert metadata["source_approval_verified"] is True
    assert metadata["synthetic_retriever"] is True
    assert metadata["results_are_benchmark"] is False
    assert metadata["publishable"] is False
    assert metadata["publication_blockers"] == ["synthetic_retriever"]


def test_report_states_the_publication_gate_and_its_blockers():
    """A reader must not have to open metadata.json to learn a run is unpublishable."""
    from benchmarks import report as report_module

    rendered = report_module.build_report(
        metadata={
            "results_are_benchmark": False,
            "publishable": False,
            "source_approval_verified": False,
            "provenance_verified": False,
            "attributable": True,
            "synthetic_retriever": False,
            "publication_blockers": ["source_approval_not_verified: source_not_activatable×130"],
            "source_approval": {
                "index_name": "cosmetics_docs_exp_v2",
                "expected_epoch": EPOCH,
                "documents_verified": 212,
                "document_count": 212,
                "source_count": 1,
                "issues": {
                    "issue_count": 130,
                    "by_code": {"source_not_activatable": 130},
                    "issues": [{"code": "source_not_activatable", "detail": "document 'x' derives UNTRUSTED"}],
                },
            },
        },
        environment={},
        summary={"configs": {}},
        coverage={},
        per_query_rows=0,
        synthetic_retriever=False,
    )

    assert "## Publication Gate" in rendered
    assert "`results_are_benchmark`: False" in rendered
    assert "source_approval_not_verified" in rendered
    assert "`source_not_activatable`×130" in rendered
    assert "Not publishable as a benchmark result" in rendered
    assert "Source approval was not verified" in rendered
