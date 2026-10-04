"""The README demo corpus must obey the repository's real RBAC semantics.

`docs/demo/` drives the README screenshot: `capture_demo.py` asks the question
once as `role_options[0]` and then again as `role_options[3]`, and the mock
backend decides what evidence each identity may see. Nothing checked that the
corpus masks actually permitted what the picture shows, so a document could be
cited to an identity that `common.auth.is_allowed` would have filtered out —
the demo would then advertise a permission boundary the service does not have.

These tests evaluate the corpus against the **real** predicate from
`common.auth`, so the fixture cannot quietly disagree with production logic.
That is the opposite of treating the mock as runtime validation: the mock is
the thing under test, and the service is the oracle. Nothing here proves RBAC
works against a live Qdrant / Elasticsearch / Redis deployment.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = REPO_ROOT / "docs" / "demo"
CORPUS_PATH = DEMO_DIR / "synthetic_corpus.json"
MOCK_API_PATH = DEMO_DIR / "mock_api.py"

# `capture_demo.py` sends turn 1 as `role_options[0]` (the UI default) and
# switches to `role_options[3]` for turn 2. If those indices move, the demo
# narrative moves with them and this file must follow.
PRIVILEGED_OPTION = 0
RESTRICTED_OPTION = 3


def _load_corpus() -> dict:
    with CORPUS_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _load_mock_api():
    """Import the stdlib-only fixture by path; `docs/demo` is not a package."""
    spec = importlib.util.spec_from_file_location("demo_mock_api", MOCK_API_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORPUS = _load_corpus()
MOCK_API = _load_mock_api()
EVIDENCE_IDS = CORPUS["evidence_doc_ids"]
DOCUMENTS = {doc["doc_id"]: doc for doc in CORPUS["documents"]}
ROLE_OPTIONS = CORPUS["auth_metadata"]["rbac"]["role_options"]
PRIVILEGED = ROLE_OPTIONS[PRIVILEGED_OPTION]
RESTRICTED = ROLE_OPTIONS[RESTRICTED_OPTION]


def _real_is_allowed(doc: dict, identity: dict) -> bool:
    from common.auth import is_allowed

    return is_allowed(doc["role_mask"], identity["role_mask"], doc["dept_mask"], identity["dept_mask"])


def _evidence_documents() -> list[dict]:
    return [DOCUMENTS[doc_id] for doc_id in EVIDENCE_IDS]


# ── the corpus is well formed ────────────────────────────────────────────
def test_every_referenced_evidence_id_exists_as_a_document():
    """A dangling id would be filtered out for a reason that has nothing to do
    with RBAC, which is exactly how the original mismatch stayed invisible."""
    assert EVIDENCE_IDS, "the demo must cite at least one evidence document"
    assert len(set(EVIDENCE_IDS)) == len(EVIDENCE_IDS), "duplicate evidence id"
    assert set(EVIDENCE_IDS) <= set(DOCUMENTS), f"unknown evidence ids: {set(EVIDENCE_IDS) - set(DOCUMENTS)}"


def test_corpus_role_and_department_masks_match_the_service_config():
    """The demo's masks only mean something if they are the service's masks."""
    from common.config import get_config

    rbac = CORPUS["auth_metadata"]["rbac"]
    cfg = get_config().rbac
    assert rbac["roles"] == cfg.roles
    assert rbac["departments"] == cfg.departments
    assert MOCK_API.SUPER_ADMIN_MASK == cfg.super_admin_mask


def test_fixture_predicate_agrees_with_the_real_one_on_every_pair():
    """`mock_api` re-implements `is_allowed` to stay stdlib-only. Pin the copy
    to the original across every document x identity pair the demo can produce,
    plus mask combinations the corpus does not currently use, so a future mask
    edit cannot make the two drift apart silently."""
    from common.auth import is_allowed

    identities = [
        (option["role_mask"], option["dept_mask"]) for option in ROLE_OPTIONS
    ] + [(0, 0), (0xFFFFFFFF, 0xFFFFFFFF)]
    doc_masks = [(doc["role_mask"], doc["dept_mask"]) for doc in DOCUMENTS.values()]
    doc_masks += [(0, 0), (0, 4), (4, 0), (6, 6)]

    for role_mask, dept_mask in identities:
        for doc_role_mask, doc_dept_mask in doc_masks:
            assert MOCK_API.is_allowed(doc_role_mask, role_mask, doc_dept_mask, dept_mask) == is_allowed(
                doc_role_mask, role_mask, doc_dept_mask, dept_mask
            ), f"fixture disagrees at doc={doc_role_mask}/{doc_dept_mask} user={role_mask}/{dept_mask}"


# ── the two identities the demo actually shows ──────────────────────────
def test_privileged_demo_identity_may_access_every_displayed_evidence_document():
    """Turn 1 of the screenshot shows both citations, so the real predicate must
    clear both documents for the identity the UI starts with."""
    for doc in _evidence_documents():
        assert _real_is_allowed(doc, PRIVILEGED), (
            f"{PRIVILEGED['key']} ({PRIVILEGED['role_mask']}/{PRIVILEGED['dept_mask']}) is shown "
            f"{doc['doc_id']} ({doc['role_mask']}/{doc['dept_mask']}) but may not read it"
        )

    assert MOCK_API.authorized_doc_ids(CORPUS, PRIVILEGED["role_mask"], PRIVILEGED["dept_mask"]) == EVIDENCE_IDS


def test_restricted_demo_identity_may_access_no_displayed_evidence_document():
    """Turn 2 is the refusal branch, which is only honest if the identity
    clears nothing at all rather than clearing part of the evidence set."""
    for doc in _evidence_documents():
        assert not _real_is_allowed(doc, RESTRICTED), (
            f"{RESTRICTED['key']} ({RESTRICTED['role_mask']}/{RESTRICTED['dept_mask']}) is refused "
            f"{doc['doc_id']} ({doc['role_mask']}/{doc['dept_mask']}) yet may read it"
        )

    assert MOCK_API.authorized_doc_ids(CORPUS, RESTRICTED["role_mask"], RESTRICTED["dept_mask"]) == []


def test_no_identity_is_handed_evidence_it_cannot_read():
    """The screenshot is only the two identities above, but the mock answers
    whatever mask the headers carry. Sweep every combination the frontend can
    emit, plus mismatched role/department pairs, the configured admin bypass
    and the anonymous identity, so no path can serve an unopenable citation.

    The mismatched pairs matter: the fixture used to decide access by comparing
    the *role* mask against one privileged constant, so any identity carrying
    that role cleared the department check too. `regulation` in
    `quality_dept` must read nothing here.
    """
    from common.auth import is_allowed

    identities = [
        (option["role_mask"], option["dept_mask"]) for option in ROLE_OPTIONS
    ] + [
        (CORPUS["auth_metadata"]["rbac"]["roles"]["admin"], 0),
        (MOCK_API.SUPER_ADMIN_MASK, 0),
        (0, 0),
        # Right role, wrong department — and role without any department.
        (PRIVILEGED["role_mask"], RESTRICTED["dept_mask"]),
        (PRIVILEGED["role_mask"], 0),
    ]
    for role_mask, dept_mask in identities:
        served = MOCK_API.authorized_doc_ids(CORPUS, role_mask, dept_mask)
        assert set(served) <= set(EVIDENCE_IDS)
        for doc_id in served:
            doc = DOCUMENTS[doc_id]
            assert is_allowed(doc["role_mask"], role_mask, doc["dept_mask"], dept_mask)


def test_department_dimension_is_enforced_not_just_the_role():
    """Named separately from the sweep so a regression reports as a permission
    bug rather than as one anonymous line item in a table."""
    for dept_mask in (RESTRICTED["dept_mask"], 0):
        assert not MOCK_API.authorized_doc_ids(CORPUS, PRIVILEGED["role_mask"], dept_mask), (
            f"role_mask={PRIVILEGED['role_mask']} with dept_mask={dept_mask} was served evidence it "
            "cannot read; the fixture is comparing the role mask only"
        )


def test_answer_and_refusal_branches_are_both_reachable():
    """The refusal copy exists to be shown; assert the corpus keeps both a
    granted and an emptied identity so neither branch becomes dead data."""
    assert "answer_refusal_markdown" in CORPUS
    assert CORPUS["answer_refusal_markdown"] != CORPUS["answer_markdown"]
    assert MOCK_API.authorized_doc_ids(CORPUS, PRIVILEGED["role_mask"], PRIVILEGED["dept_mask"])
    assert not MOCK_API.authorized_doc_ids(CORPUS, RESTRICTED["role_mask"], RESTRICTED["dept_mask"])


# ── the endpoint itself ─────────────────────────────────────────────────
@pytest.fixture(scope="module")
def demo_server():
    """Run the real fixture on an ephemeral port and yield its base URL."""
    MOCK_API.DemoHandler.corpus = MOCK_API.load_corpus()
    server = ThreadingHTTPServer(("127.0.0.1", 0), MOCK_API.DemoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _ask(base_url: str, identity: dict) -> dict:
    request = urllib.request.Request(  # noqa: S310 - loopback test server
        f"{base_url}/api/query",
        data=json.dumps({"query": CORPUS["query"]["text"]}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-User-ID": CORPUS["auth_metadata"]["auth"]["anonymous_user_id"],
            "X-Role-Mask": str(identity["role_mask"]),
            "X-Dept-Mask": str(identity["dept_mask"]),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback test server
        return json.loads(response.read().decode("utf-8"))


def test_query_endpoint_returns_only_authorized_evidence(demo_server):
    """Requirement under test: the response must never name a document the
    calling identity cannot open. Checked over the HTTP boundary, not against
    the helper, so a wiring mistake in the handler is caught too."""
    answered = _ask(demo_server, PRIVILEGED)
    assert answered["evidence_doc_ids"] == EVIDENCE_IDS
    assert answered["answer"] == CORPUS["answer_markdown"]

    refused = _ask(demo_server, RESTRICTED)
    assert refused["evidence_doc_ids"] == []
    assert refused["answer"] == CORPUS["answer_refusal_markdown"]


def test_query_endpoint_denies_anonymous_and_malformed_masks(demo_server):
    """Mask 0 is what an absent or unparsable header collapses to; it must clear
    nothing rather than defaulting to the demo's privileged identity."""
    for headers in (
        {"X-Role-Mask": "0", "X-Dept-Mask": "0"},
        {"X-Role-Mask": "not-a-mask", "X-Dept-Mask": "not-a-mask"},
        {"X-Role-Mask": "-4", "X-Dept-Mask": "-4"},
        {},
    ):
        request = urllib.request.Request(  # noqa: S310 - loopback test server
            f"{demo_server}/api/query",
            data=json.dumps({"query": CORPUS["query"]["text"]}).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
        assert body["evidence_doc_ids"] == [], f"leaked evidence for headers {headers}"
        assert body["answer"] == CORPUS["answer_refusal_markdown"]
