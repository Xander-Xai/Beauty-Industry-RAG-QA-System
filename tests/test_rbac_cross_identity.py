"""RBAC regression across roles, departments, cache partitions and fusion order.

These tests use the real authorization, cache-key and filtering code with
in-process fakes for the stores. They prove **pipeline behaviour**, not
real-engine behaviour: an in-memory Qdrant or a fake Redis cannot show what a
real server does with a filter or a key. That distinction is asserted at the
bottom of this file so the file cannot be misread as store validation.

The threat they cover: an answer built from documents the caller may not read.
Every assertion is expressed as "this document must never reach the caller",
because a leak is the failure that matters — a spurious refusal is a cost, a
leak is a breach.
"""

from __future__ import annotations

import pytest

from cache.redis_cache import RedisCache
from common.auth import is_document_authorized

#: Two roles and two departments, disjoint on both axes.
ROLE_REGULATOR = 0b0001
ROLE_DEVELOPER = 0b0010
ROLE_ADMIN = 0b0100
DEPT_A = 0b0001
DEPT_B = 0b0010

#: A document visible only to the regulator role in department A.
REGULATOR_ONLY = {"role_mask": ROLE_REGULATOR, "dept_mask": DEPT_A}
DEVELOPER_ONLY = {"role_mask": ROLE_DEVELOPER, "dept_mask": DEPT_B}
PUBLIC = {"role_mask": 0, "dept_mask": 0}
ALL_ROLES_ALL_DEPTS = {"role_mask": 0b0111, "dept_mask": 0b0011}


class _Candidate:
    """Minimal stand-in for a retrieved candidate carrying RBAC metadata."""

    def __init__(self, doc_id: str, metadata: dict) -> None:
        self.doc_id = doc_id
        self.content = f"content for {doc_id}"
        self.score = 1.0
        self.source = "bm25_es"
        self.metadata = metadata


def _candidates() -> list[_Candidate]:
    return [
        _Candidate("public-doc", PUBLIC),
        _Candidate("regulator-doc", REGULATOR_ONLY),
        _Candidate("developer-doc", DEVELOPER_ONLY),
    ]


# ── the authorization predicate ─────────────────────────────────────────────


def test_authorized_identity_is_allowed():
    assert is_document_authorized(REGULATOR_ONLY, ROLE_REGULATOR, DEPT_A) is True


def test_wrong_role_is_denied():
    assert is_document_authorized(REGULATOR_ONLY, ROLE_DEVELOPER, DEPT_A) is False


def test_wrong_department_is_denied():
    """Role alone is not enough — the department axis is independent."""
    assert is_document_authorized(REGULATOR_ONLY, ROLE_REGULATOR, DEPT_B) is False


def test_public_document_is_visible_to_every_valid_identity():
    for role, dept in ((ROLE_REGULATOR, DEPT_A), (ROLE_DEVELOPER, DEPT_B), (ROLE_ADMIN, 0)):
        assert is_document_authorized(PUBLIC, role, dept) is True


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"role_mask": None, "dept_mask": DEPT_A},
        {"role_mask": ROLE_REGULATOR},
        {"role_mask": -1, "dept_mask": DEPT_A},
        {"role_mask": 2**32, "dept_mask": DEPT_A},
        {"role_mask": True, "dept_mask": DEPT_A},
        {"role_mask": "1", "dept_mask": DEPT_A},
    ],
)
def test_malformed_metadata_fails_closed(metadata):
    """Anything not a clean uint32 pair is denied, never coerced."""
    assert is_document_authorized(metadata, ROLE_REGULATOR, DEPT_A) is False


def test_malformed_user_mask_fails_closed():
    assert is_document_authorized(PUBLIC, None, DEPT_A) is False
    assert is_document_authorized(PUBLIC, ROLE_REGULATOR, "0") is False


# ── pre- and post-fusion filtering ──────────────────────────────────────────


def _filter(results, role, dept):
    """The production post-fusion filter, applied directly.

    ``_apply_rbac_filter`` is called unbound because it reads no instance state;
    constructing the manager would only add lazy retriever properties that this
    test deliberately does not want to touch.
    """
    from retrieval.parallel_recall import ParallelRecallManager

    return ParallelRecallManager._apply_rbac_filter(None, results, role, dept)


def test_post_fusion_filter_removes_documents_the_caller_may_not_read():
    visible = _filter(_candidates(), ROLE_REGULATOR, DEPT_A)
    assert {item.doc_id for item in visible} == {"public-doc", "regulator-doc"}


def test_post_fusion_filter_isolates_departments():
    """The same query, a different department, a disjoint document set."""
    visible = _filter(_candidates(), ROLE_DEVELOPER, DEPT_B)
    assert {item.doc_id for item in visible} == {"public-doc", "developer-doc"}


def test_no_document_visible_to_two_identities_at_once_is_the_whole_risk():
    """Two callers must never receive the same private document."""
    regulator = {item.doc_id for item in _filter(_candidates(), ROLE_REGULATOR, DEPT_A)}
    developer = {item.doc_id for item in _filter(_candidates(), ROLE_DEVELOPER, DEPT_B)}
    assert regulator & developer == {"public-doc"}


def test_admin_masks_do_not_grant_documents_the_department_axis_denies():
    """A wide role mask is not a bypass of the department axis."""
    visible = _filter(_candidates(), 0b0111, 0b0010)
    assert "regulator-doc" not in {item.doc_id for item in visible}


def test_filtering_a_candidate_with_no_metadata_denies_it():
    class _Bare:
        doc_id = "bare"
        metadata = None

    assert _filter([_Bare()], ROLE_ADMIN, 0b0111) == []


# ── fusion cannot reintroduce a filtered document ───────────────────────────


def test_rerank_and_fusion_do_not_resurrect_a_denied_document():
    """The final whole-list filter is what closes a pre-fusion pushdown gap.

    Filtering runs per path before fusion *and* again over the fused result. If
    only the per-path filter existed, a document that one path returned without
    authorization (because the store-side pushdown did not apply) would re-enter
    through the fused list and reach the caller. This drives that exact case.
    """
    from retrieval_service.rerank.rrf_fusion import rrf_fusion

    # The regulator's legitimate view.
    regulator_visible = {"public-doc", "regulator-doc"}

    # A dense path that did not honour the pushdown and returned a document from
    # another department, plus a legitimate one.
    leaky_dense = [
        _Candidate("developer-doc", DEVELOPER_ONLY),
        _Candidate("regulator-doc", REGULATOR_ONLY),
    ]
    bm25_path = [_Candidate("public-doc", PUBLIC)]

    fused = rrf_fusion({"dense_bge": leaky_dense, "bm25_es": bm25_path}, k=60)
    fused_ids = {item.doc_id for item in fused}
    # The leak is present *before* the final filter — that is the gap being closed.
    assert "developer-doc" in fused_ids

    # The post-fusion pass removes it, and keeps exactly the legitimate view.
    final = _filter(fused, ROLE_REGULATOR, DEPT_A)
    assert {item.doc_id for item in final} == regulator_visible

    # And a document injected directly into the fused list is caught too.
    leaked = _filter(_candidates() + [_Candidate("injected", DEVELOPER_ONLY)], ROLE_REGULATOR, DEPT_A)
    assert "injected" not in {item.doc_id for item in leaked}


# ── cache partitioning ──────────────────────────────────────────────────────


def test_cache_key_differs_per_role_and_department():
    """Identical query text must not share a cache entry across identities."""
    manager = RedisCache
    base = dict(normalized_query="烟酰胺限量?", embedding_version="bge", knowledge_version_epoch="e1")
    regulator = manager.compute_cache_key(**base, role_mask=ROLE_REGULATOR, dept_mask=DEPT_A)
    developer = manager.compute_cache_key(**base, role_mask=ROLE_DEVELOPER, dept_mask=DEPT_B)
    same_role_other_dept = manager.compute_cache_key(**base, role_mask=ROLE_REGULATOR, dept_mask=DEPT_B)
    other_role_same_dept = manager.compute_cache_key(**base, role_mask=ROLE_DEVELOPER, dept_mask=DEPT_A)

    keys = {regulator, developer, same_role_other_dept, other_role_same_dept}
    assert len(keys) == 4, "every identity must occupy its own cache partition"


def test_cache_key_is_stable_for_the_same_identity():
    manager = RedisCache
    args = dict(
        normalized_query="烟酰胺限量?",
        embedding_version="bge",
        knowledge_version_epoch="e1",
        role_mask=ROLE_REGULATOR,
        dept_mask=DEPT_A,
    )
    assert manager.compute_cache_key(**args) == manager.compute_cache_key(**args)


def test_l2_storage_key_includes_the_permission_partition():
    """The physical key must carry the masks, not just the logical key."""
    manager = RedisCache
    logical = manager.compute_cache_key(normalized_query="q", role_mask=ROLE_REGULATOR, dept_mask=DEPT_A)
    regulator_key = manager._build_l2_storage_key(logical, ROLE_REGULATOR, DEPT_A)
    developer_key = manager._build_l2_storage_key(logical, ROLE_DEVELOPER, DEPT_B)
    assert regulator_key != developer_key
    assert str(ROLE_REGULATOR) in regulator_key
    assert str(DEPT_A) in regulator_key


@pytest.mark.parametrize("bad", [True, False, "1", None, -1, 2**32, 1.0])
def test_cache_rejects_non_uint32_permission_identities(bad):
    """A bool or a string must not alias a legitimate mask.

    ``True == 1`` and ``"1"`` both stringify to the same Redis key as the
    integer 1, so without this check two different principals could share a
    partition.
    """
    with pytest.raises(ValueError):
        RedisCache._validate_permission_scope(bad, DEPT_A)
    with pytest.raises(ValueError):
        RedisCache._validate_permission_scope(ROLE_REGULATOR, bad)


def test_pipeline_cache_key_includes_the_masks():
    """The pipeline builds its own key; it must include the identity too."""

    class _Ctx:
        user_input = "烟酰胺限量?"
        user_role_mask = ROLE_REGULATOR
        user_dept_mask = DEPT_A
        rewrite_result = None
        session_id = None

    from core.pipeline import OnlineRAGPipeline

    regulator_key = OnlineRAGPipeline._build_cache_key(None, _Ctx())

    class _OtherCtx(_Ctx):
        user_role_mask = ROLE_DEVELOPER
        user_dept_mask = DEPT_B

    developer_key = OnlineRAGPipeline._build_cache_key(None, _OtherCtx())
    assert regulator_key != developer_key


# ── scope of the evidence this file provides ─────────────────────────────────


def test_these_are_in_process_tests_not_real_store_validation():
    """States the limit of the file above it.

    Everything here runs against dicts and fake candidates. No Qdrant client and
    no Redis connection is created, so this file cannot be cited as store-level
    RBAC validation — that is `VAL-STORE-001`, tracked in
    docs/deferred-runtime-validation.md.
    """
    import ast
    import inspect

    import tests.test_rbac_cross_identity as module

    tree = ast.parse(inspect.getsource(module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    # The stores under test are never constructed here; only their key/filter
    # logic is exercised, so a real-engine claim cannot be inferred from this.
    assert "qdrant_client" not in imported
    assert "redis" not in imported

    source = inspect.getsource(module)
    assert "not real store" in source.lower() or "real engine" in source.lower()
