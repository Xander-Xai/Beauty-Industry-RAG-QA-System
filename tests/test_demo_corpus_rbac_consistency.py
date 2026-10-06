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

import copy
import importlib.util
import json
import threading
import urllib.error
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

    identities = [(option["role_mask"], option["dept_mask"]) for option in ROLE_OPTIONS] + [
        (0, 0),
        (0xFFFFFFFF, 0xFFFFFFFF),
    ]
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

    identities = [(option["role_mask"], option["dept_mask"]) for option in ROLE_OPTIONS] + [
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


# ── the media endpoint is the second authorization pass ──────────────────
def _fetch_media(base_url: str, doc_id: str, headers: dict) -> tuple[int, dict]:
    """GET a media URL and return ``(status, body)`` instead of raising on 4xx."""
    request = urllib.request.Request(  # noqa: S310 - loopback test server
        f"{base_url}/api/media/{doc_id}",
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback test server
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _identity_headers(identity: dict) -> dict:
    return {
        "X-User-ID": CORPUS["auth_metadata"]["auth"]["anonymous_user_id"],
        "X-Role-Mask": str(identity["role_mask"]),
        "X-Dept-Mask": str(identity["dept_mask"]),
    }


def test_fixture_media_predicate_agrees_with_the_real_one_on_every_pair():
    """`mock_api` mirrors `common.auth.is_document_authorized` for the media
    path. Pin the copy to the original across the same document x identity grid
    the query path uses, so the two mirrors cannot drift apart."""
    from common.auth import is_document_authorized

    identities = [(option["role_mask"], option["dept_mask"]) for option in ROLE_OPTIONS] + [
        (0, 0),
        (CORPUS["auth_metadata"]["rbac"]["roles"]["admin"], 0),
        (MOCK_API.SUPER_ADMIN_MASK, 0xFFFFFFFF),
        (PRIVILEGED["role_mask"], RESTRICTED["dept_mask"]),
    ]
    for doc in DOCUMENTS.values():
        for role_mask, dept_mask in identities:
            expected = is_document_authorized(doc, role_mask, dept_mask)
            assert (
                MOCK_API.is_document_authorized(doc["role_mask"], role_mask, doc["dept_mask"], dept_mask) == expected
            ), f"fixture disagrees at doc={doc['doc_id']} user={role_mask}/{dept_mask}"


def test_media_endpoint_grants_the_privileged_identity_and_refuses_the_restricted_one(demo_server):
    """The regression: this handler used to check only that the document id
    existed, so the identity the query endpoint refuses could still fetch the
    media URL — while the generated caption claims the click performs
    server-side secondary authorization."""
    for doc_id in EVIDENCE_IDS:
        status, body = _fetch_media(demo_server, doc_id, _identity_headers(PRIVILEGED))
        assert status == 200, (doc_id, status, body)
        assert body["doc_id"] == doc_id

        status, body = _fetch_media(demo_server, doc_id, _identity_headers(RESTRICTED))
        assert status == 403, (doc_id, status, body)
        assert body["error"] == "forbidden"
        assert "url" not in body


def test_media_endpoint_refuses_anonymous_and_malformed_masks(demo_server):
    """Mask 0 collapses an absent or unparsable header to an identity that
    clears nothing. Defaulting to the demo's privileged identity here would
    make the endpoint a way around the query filter."""
    doc_id = EVIDENCE_IDS[0]
    for headers in (
        {"X-Role-Mask": "0", "X-Dept-Mask": "0"},
        {"X-Role-Mask": "not-a-mask", "X-Dept-Mask": "not-a-mask"},
        {"X-Role-Mask": "-4", "X-Dept-Mask": "-4"},
        {},
    ):
        status, body = _fetch_media(demo_server, doc_id, headers)
        assert status == 403, (headers, status, body)
        assert "url" not in body


def test_media_endpoint_still_reports_an_unknown_document_as_not_found(demo_server):
    """Existence is checked before authorization, matching `media_handler`
    (404 then 403). A 403 for a document that does not exist would leak the
    id space; a 200 would be worse."""
    status, body = _fetch_media(demo_server, "demo_does_not_exist", _identity_headers(PRIVILEGED))
    assert status == 404
    assert body["error"] == "not_found"


def test_no_document_is_readable_through_media_by_an_identity_the_query_refuses(demo_server):
    """The two endpoints must agree. If the query hides a document but media
    serves it, the permission boundary the demo advertises does not exist."""
    for identity in ROLE_OPTIONS:
        for doc_id in EVIDENCE_IDS:
            status, _ = _fetch_media(demo_server, doc_id, _identity_headers(identity))
            readable = status == 200
            query_body = _ask(demo_server, identity)
            assert readable == (doc_id in query_body["evidence_doc_ids"]), (
                f"{identity['key']}: media status {status} disagrees with the query evidence set for {doc_id}"
            )


# ── the two response shapes are different, and both are honoured ────────
def _post(base_url: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(  # noqa: S310 - loopback test server
        f"{base_url}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Role-Mask": str(PRIVILEGED["role_mask"]),
            "X-Dept-Mask": str(PRIVILEGED["dept_mask"]),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback test server
        return json.loads(response.read().decode("utf-8"))


def test_query_returns_a_query_response_without_history(demo_server):
    """`api/models.py::QueryResponse` has no `history` field. Adding one would be
    as much a shape lie as omitting one from ChatResponse."""
    body = _post(demo_server, "/api/query", {"query": CORPUS["query"]["text"]})

    assert "history" not in body
    for field in ("answer", "session_id", "business_type", "intent", "evidence_doc_ids", "latency_ms", "cache_hit"):
        assert field in body, field


def test_chat_returns_a_chat_response_with_history(demo_server):
    """The regression: both endpoints shared one branch that returned the query
    shape, so `/api/chat` omitted the `history` field the contract requires. The
    current UI happens not to read it, which is exactly why nothing caught it."""
    body = _post(demo_server, "/api/chat", {"message": CORPUS["query"]["text"]})

    assert "history" in body, sorted(body)
    assert isinstance(body["history"], list)
    for message in body["history"]:
        assert set(message) == {"role", "content"}, message
        assert message["role"] in ("user", "assistant"), message
        assert isinstance(message["content"], str) and message["content"].strip(), message


def test_chat_history_ends_with_the_exchange_that_was_just_answered(demo_server):
    body = _post(demo_server, "/api/chat", {"message": CORPUS["query"]["text"]})

    assert body["history"][-1] == {"role": "assistant", "content": body["answer"]}
    assert body["history"][-2] == {"role": "user", "content": CORPUS["query"]["text"]}


def test_chat_history_echoes_prior_turns_the_client_sent(demo_server):
    """A caller that keeps its own transcript must see it reflected back, not
    silently replaced by the corpus's stored round."""
    prior = [{"role": "user", "content": "earlier question"}, {"role": "assistant", "content": "earlier answer"}]

    body = _post(demo_server, "/api/chat", {"message": CORPUS["query"]["text"], "history": prior})

    assert body["history"][:2] == prior


def test_chat_history_is_capped_at_the_documented_six_rounds(demo_server):
    """`ChatResponse.history` is documented as the last 6 rounds, i.e. 12
    messages. A client sending a long transcript gets a bounded answer."""
    long_prior = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(40)]

    body = _post(demo_server, "/api/chat", {"message": CORPUS["query"]["text"], "history": long_prior})

    assert len(body["history"]) == 12
    assert body["history"][-1] == {"role": "assistant", "content": body["answer"]}


def test_chat_history_uses_the_corpus_round_when_the_client_sends_none(demo_server):
    """Fallback comes from the synthetic corpus, never from an invented exchange."""
    body = _post(demo_server, "/api/chat", {"message": CORPUS["query"]["text"]})

    stored = CORPUS["dialog_history"]["rounds"][0]
    assert {"role": "user", "content": stored["user_input"]} in body["history"]
    assert {"role": "assistant", "content": stored["response"]} in body["history"]


def test_the_refusal_branch_also_returns_a_chat_history(demo_server):
    """A refused turn is still a completed exchange; the shape must not depend on
    whether evidence was found."""
    request = urllib.request.Request(  # noqa: S310 - loopback test server
        f"{demo_server}/api/chat",
        data=json.dumps({"message": CORPUS["query"]["text"]}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Role-Mask": str(RESTRICTED["role_mask"]),
            "X-Dept-Mask": str(RESTRICTED["dept_mask"]),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback test server
        body = json.loads(response.read().decode("utf-8"))

    assert body["answer"] == CORPUS["answer_refusal_markdown"]
    assert body["history"][-1] == {"role": "assistant", "content": CORPUS["answer_refusal_markdown"]}


def test_both_endpoints_validate_against_the_real_response_models(demo_server):
    """The strongest form of "the shapes mirror the contracts": parse the live
    fixture output with the service's own Pydantic models.

    Nothing else in the suite would notice a fixture drifting from
    `api/models.py`, because the current UI tolerates extra and missing fields —
    which is exactly how the missing `history` survived review in the first place.
    """
    from api.models import ChatResponse, QueryResponse

    query = _post(demo_server, "/api/query", {"query": CORPUS["query"]["text"]})
    chat = _post(demo_server, "/api/chat", {"message": CORPUS["query"]["text"]})

    assert QueryResponse(**query).evidence_doc_ids == EVIDENCE_IDS
    parsed = ChatResponse(**chat)
    assert parsed.evidence_doc_ids == EVIDENCE_IDS
    assert parsed.history and parsed.history[-1].role == "assistant"


# ── the walkthrough caption must not borrow another path's guarantee ─────
def _identity_caption() -> str:
    for row in CORPUS["trace"]["rows"]:
        if row["step"] == "身份解析":
            return row["demo"]
    raise AssertionError("the trace table has no 身份解析 row")


def test_the_identity_caption_does_not_claim_uint32_checks_on_the_dev_header_path():
    """The regression this caption was corrected for, and the one it must not return to.

    The caption read "uint32 校验，畸形声明 fail closed" beside a pointer to the
    identity resolver while the requests the demo actually makes use the dev-mode
    headers — and `common/auth.py` parsed those with a bare `int(...)`, with no
    range check, so `-1` passed and `-1 & mask` overlaps every document mask.

    The caption was corrected to say the dev-header path was an unbounded
    `int()` read. That correction has itself been overtaken: the dev-header path
    now shares the canonical uint32 validator, so the honest caption may claim
    the check again — but only because the code below now enforces it. This test
    keeps the honesty property in force in whichever direction is currently true.
    """
    caption = _identity_caption()
    enforces = _dev_header_path_enforces_the_uint32_contract()

    assert ("共用同一套 uint32 校验" in caption) is enforces, caption


def test_the_identity_caption_still_credits_the_jwt_path_for_uint32_validation():
    """Correcting the claim must not delete the part that is true."""
    assert "uint32" in _identity_caption(), _identity_caption()


def _dev_header_path_enforces_the_uint32_contract() -> bool:
    """Behavioural check that the dev-header branch really applies the uint32 range.

    Reads it off the running code rather than off the source text, so the check
    cannot be satisfied by a comment, a dead branch, or a bound that some other
    layer happens to add.
    """
    from common.auth import _identity_from_dev_headers

    headers = {"X-Role-Mask": "-1", "X-Dept-Mask": "-1"}
    try:
        _identity_from_dev_headers(_StubRequest(headers))
    except ValueError:
        return True
    return False


class _StubRequest:
    """Header-mapping stand-in for the identity-ingress helpers."""

    def __init__(self, headers: dict):
        self.headers = headers


def test_the_dev_header_path_really_is_bounded_by_the_canonical_validator():
    """The caption's uint32 claim is only honest if the code matches it.

    Pins the current behaviour of `common/auth.py`'s dev-mode branch so that a
    future change to that path has to update the caption in the same change,
    rather than leaving the figure quietly stale in the other direction.
    """
    import inspect

    from common.auth import _identity_from_dev_headers, _identity_from_jwt, parse_identity

    source = inspect.getsource(parse_identity)
    dev_branch = source[source.index("# 2. Dev-mode headers") :]
    assert "int(role_str)" not in dev_branch, "the dev-header branch parses headers directly again"
    assert "_identity_from_dev_headers" in dev_branch, "the dev-header branch stopped using the shared ingress"

    # Behavioural bound, at both edges of the canonical range.
    for accepted in ("0", "4294967295"):
        identity = _identity_from_dev_headers(_StubRequest({"X-Role-Mask": accepted, "X-Dept-Mask": accepted}))
        assert identity.user_role_mask == int(accepted)

    for refused in ("-1", "4294967296"):
        with pytest.raises(ValueError):
            _identity_from_dev_headers(_StubRequest({"X-Role-Mask": refused, "X-Dept-Mask": refused}))

    # One validator, not two look-alike rule sets: the same verdict on both paths.
    with pytest.raises(ValueError):
        _identity_from_jwt({"sub": "u", "role_mask": -1, "dept_mask": -1})


def test_the_mock_fixture_is_stricter_than_the_service_and_says_so():
    """The fixture deliberately collapses a bad header to 0. That is a fixture
    choice, and the module has to own it rather than let the figure imply the
    service behaves the same way."""
    source = (DEMO_DIR / "mock_api.py").read_text(encoding="utf-8")

    assert "stricter on purpose" in source


def _cited_files(row: dict) -> list[Path]:
    """Every file a trace row cites, split on the " + " separator."""
    return [REPO_ROOT / part.strip() for part in row["code"].split("+")]


def _cited_paths(row: dict) -> set[str]:
    """The cited paths as written in the corpus, for comparison."""
    return {part.strip() for part in row["code"].split("+")}


def test_every_trace_row_points_at_a_file_that_exists_and_is_the_real_implementation():
    """The README claims each annotation row points at the file that really
    implements the step. `api/dependencies.py` was cited for identity resolution
    while being only a backward-compatibility alias for `common.auth.parse_identity`
    — so the row sent a reader to a shim.

    Aliases are rejected as well as missing paths: a file that exists but says of
    itself that it only re-exports the real implementation is not where the
    behaviour lives.
    """
    for row in CORPUS["trace"]["rows"]:
        for target in _cited_files(row):
            assert target.is_file(), f"{row['step']} points at missing {target.relative_to(REPO_ROOT)}"
        source = _cited_files(row)[0].read_text(encoding="utf-8")
        if "backward" in source.lower() and "alias" in source.lower():
            pytest.fail(
                f"{row['step']} points at {row['code']}, which is only a backward-compatibility alias; "
                "cite the module that implements it"
            )


def test_the_identity_row_points_at_the_module_that_parses_masks():
    """Named separately so a regression reports as a wrong pointer rather than as
    one anonymous line item in a loop."""
    row = next(row for row in CORPUS["trace"]["rows"] if row["step"] == "身份解析")

    assert row["code"] == "common/auth.py", row["code"]
    source = (REPO_ROOT / row["code"]).read_text(encoding="utf-8")
    assert "_identity_from_dev_headers" in source, "the cited module is no longer the one reading dev-mode masks"
    assert "_validate_permission_mask_claim" in source, "the cited module no longer holds the shared mask validator"


def test_a_row_claiming_two_stores_cites_both_implementations():
    """The regression: the storage row claimed "ES: status+epoch+role_mask+dept_mask"
    while citing only `common/auth.py`, which builds the *Qdrant* filter and
    delegates nothing to Elasticsearch. `BM25Retriever._build_es_query` is where
    the ES filters live, so following the pointer could not verify half the claim.
    """
    row = next(row for row in CORPUS["trace"]["rows"] if row["step"] == "存储侧下推")

    cited = _cited_paths(row)
    assert "common/auth.py" in cited, row["code"]
    assert "retrieval/bm25_retriever.py" in cited, (
        f"the ES half of {row['demo']} is implemented elsewhere: {row['code']}"
    )


def test_each_cited_file_really_implements_part_of_the_row_it_is_cited_for():
    """Named per file so the check is about the claim, not about the path
    existing: the ES filters must be visible in the file the row now names."""
    row = next(row for row in CORPUS["trace"]["rows"] if row["step"] == "存储侧下推")
    by_path = {part.strip(): (REPO_ROOT / part.strip()).read_text(encoding="utf-8") for part in row["code"].split("+")}

    assert "build_qdrant_filter" in by_path["common/auth.py"], "the Qdrant helper moved"
    es_source = by_path["retrieval/bm25_retriever.py"]
    assert "_build_es_query" in es_source, "the ES query builder moved"
    for field in ("status", "role_mask", "dept_mask"):
        assert field in es_source, f"the cited ES builder no longer filters on {field}"


# ── the disclosures that make the image honest are enforced ─────────────
def _capture_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("demo_capture_disclosures", DEMO_DIR / "capture_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAPTURE_SCRIPT = _capture_module()


def test_the_shipped_corpus_satisfies_its_own_disclosure_requirement():
    """Nothing to assert about the rule here — that the corpus as committed
    passes it. Every other test in this file assumes it does."""
    CAPTURE_SCRIPT.require_disclosures(CORPUS)


def test_removing_the_answer_marker_is_refused():
    """The regression: `docs/demo/README.md` said the script refuses a corpus
    whose synthetic markers are deleted, and nothing checked. The badge and
    captions are baked into the generator, but the marker inside each answer and
    the subtitle the real frontend renders come from the JSON — so an edit there
    produced a committed screenshot with no label on it.
    """
    stripped = copy.deepcopy(CORPUS)
    stripped["answer_markdown"] = stripped["answer_markdown"].replace("\uff08DEMO\uff1a", "(")

    with pytest.raises(SystemExit) as excinfo:
        CAPTURE_SCRIPT.require_disclosures(stripped)
    assert "answer_markdown" in str(excinfo.value)


def test_removing_the_refusal_marker_is_refused():
    """The refusal branch is rendered too, so its marker matters as much."""
    stripped = copy.deepcopy(CORPUS)
    stripped["answer_refusal_markdown"] = stripped["answer_refusal_markdown"].replace("\uff08DEMO\uff1a", "(")

    with pytest.raises(SystemExit):
        CAPTURE_SCRIPT.require_disclosures(stripped)


def test_replacing_the_subtitle_with_a_plausible_one_is_refused():
    """Not only deleting it. An ordinary-looking subtitle is the more dangerous
    edit, because the screenshot still renders and still looks right."""
    relabelled = copy.deepcopy(CORPUS)
    relabelled["auth_metadata"]["app"]["subtitle"] = (
        "\u5316\u5986\u54c1\u884c\u4e1a\u77e5\u8bc6\u95ee\u7b54\u52a9\u624b"
    )

    with pytest.raises(SystemExit) as excinfo:
        CAPTURE_SCRIPT.require_disclosures(relabelled)
    assert "subtitle" in str(excinfo.value)


@pytest.mark.parametrize("field", ["answer_markdown", "answer_refusal_markdown", "auth_metadata"])
def test_a_missing_field_is_refused_rather_than_crashing(field):
    """A fixture edit that removes a key should produce the intended refusal, not
    a KeyError traceback from inside the checker."""
    incomplete = copy.deepcopy(CORPUS)
    if field == "auth_metadata":
        incomplete["auth_metadata"].pop("app")
    else:
        incomplete.pop(field)

    with pytest.raises(SystemExit) as excinfo:
        CAPTURE_SCRIPT.require_disclosures(incomplete)
    assert field.split(".")[0] in str(excinfo.value)


def test_every_missing_disclosure_is_reported_at_once():
    """One run should list everything that is wrong, not surface them one
    capture attempt at a time."""
    broken = copy.deepcopy(CORPUS)
    broken["answer_markdown"] = "no marker here"
    broken["answer_refusal_markdown"] = "no marker here"
    broken["auth_metadata"]["app"]["subtitle"] = "plain"

    with pytest.raises(SystemExit) as excinfo:
        CAPTURE_SCRIPT.require_disclosures(broken)

    message = str(excinfo.value)
    for field in ("answer_markdown", "answer_refusal_markdown", "subtitle"):
        assert field in message, field


def test_the_check_runs_before_anything_is_rendered(tmp_path, monkeypatch):
    """Ordering is the point: refusing after the servers are up would still have
    started them, and refusing after the image is written would already have
    produced the unlabelled artifact."""
    import json as _json
    import sys

    started: list[str] = []
    monkeypatch.setattr(CAPTURE_SCRIPT, "CORPUS_PATH", tmp_path / "synthetic_corpus.json")
    stripped = copy.deepcopy(CORPUS)
    stripped["answer_markdown"] = "no marker"
    (tmp_path / "synthetic_corpus.json").write_text(_json.dumps(stripped, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(CAPTURE_SCRIPT, "start_mock_api", lambda *a, **k: started.append("mock") or None)
    monkeypatch.setattr(CAPTURE_SCRIPT, "start_frontend", lambda *a, **k: started.append("frontend") or None)
    # Everything past the servers is stubbed too: if the guard is ever removed, the
    # failure should be a fast assertion, not a real Chromium launch against a port
    # nothing is listening on.
    monkeypatch.setattr(CAPTURE_SCRIPT, "drive_frontend", lambda *a, **k: "PNG")
    monkeypatch.setattr(CAPTURE_SCRIPT, "capture", lambda app, fonts, out, quality: (out.write_bytes(b"x"), out)[1])
    # `--no-font-download` so a regression fails without reaching for the network:
    # a unit test that starts fetching a font subset when its guard is removed is
    # slow and non-hermetic exactly when it is most needed.
    monkeypatch.setattr(
        sys,
        "argv",
        ["capture_demo.py", "--out", str(tmp_path / "hero.webp"), "--no-font-download"],
    )

    with pytest.raises(SystemExit) as excinfo:
        CAPTURE_SCRIPT.main()

    assert "answer_markdown" in str(excinfo.value)
    assert started == [], f"servers were started before the refusal: {started}"
