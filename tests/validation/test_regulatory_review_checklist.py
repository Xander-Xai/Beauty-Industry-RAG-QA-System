"""The regulatory review checklist must not be promotable by accident.

`docs/finalization/DATA_REVIEW_BACKLOG.md` names the regulatory claims in this
repository that need a qualified human to judge. Before this change they were
prose rows: no per-assertion state, no reviewer, no source, no version. Nothing
stopped anything from being *marked reviewed* — the fields were all editable and
no rule said which edit was legitimate, so "verified" could be typed by a process
that never read a regulation.

These tests pin the rules that make the state mean something. The one that
matters most is that a synthetic placeholder can never reach
`VERIFIED_WITH_SOURCE`: verifying text that was deliberately fabricated would
restate fiction as regulatory truth, which is the opposite of reviewing it and
would produce exactly the half-real corpus the project boundaries forbid.

The second that matters: no promotion is reachable without a named human, a date
and a *versioned* source, so the checklist cannot fill those fields in on a
reviewer's behalf.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.validation.regulatory_review import (
    ASSERTION_TYPES,
    LICENSE_NO_REDISTRIBUTION,
    LICENSE_PUBLIC,
    LICENSE_UNKNOWN,
    REVIEW_STATUS_BLOCKED_BY_LICENSE,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REJECTED,
    REVIEW_STATUS_SYNTHETIC_ONLY,
    REVIEW_STATUS_VERIFIED,
    REVIEW_STATUSES,
    ReviewError,
    load_checklist,
    promote_to_verified,
    render_markdown,
    summarize,
    validate_checklist,
    validate_entry,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKLIST_PATH = REPO_ROOT / "docs" / "finalization" / "REGULATORY_REVIEW_CHECKLIST.json"

#: The claim families this repository's fixtures assert, pinned here rather than
#: in the checklist file.
#:
#: `--check` validates schema and transitions; it cannot tell that a *whole row*
#: was deleted, because a checklist with fifteen entries and a checklist with
#: fourteen both look internally consistent. Deleting a row is precisely how a
#: claim would silently lose its review state, so coverage is asserted against
#: an inventory held in reviewed code — a file anyone can edit cannot guard its
#: own completeness.
EXPECTED_CLAIM_FAMILIES = frozenset(
    {
        "heavy-metal-limits",
        "niacinamide-max-concentration",
        "retinol-leave-on-rinse-off",
        "salicylic-acid-leave-on",
        "mit-preservative-restriction",
        "arbutin-kojic-azelaic",
        "aha-concentration-range",
        "filing-and-registration-regime",
        "efficacy-claim-evidence-types",
        "label-and-inci-thresholds",
        "multiplier-efficacy-claims",
        "packaging-food-contact-standard",
        "training-adapter-regulatory-capability",
        "answer-eval-citation-refusal",
        "annotation-workpackage-candidate-contexts",
    }
)

AUTHORITATIVE_SOURCE = "《化妆品安全技术规范》(示例来源，仅用于测试)"
SOURCE_VERSION = "2015 版"


def _entry(**overrides: Any) -> dict:
    """A minimal entry that is valid and awaiting review."""
    entry = {
        "id": "example-claim",
        "data_file": "tests/evaluation/golden_set.jsonl",
        "locator": "sample_id 0000",
        "regulatory_shaped_field": "question",
        "assertion_type": "LIMIT_VALUE",
        "is_explicit_synthetic_placeholder": False,
        "standalone_misreading_risk": "Low.",
        "authoritative_source": None,
        "source_version": None,
        "license": LICENSE_PUBLIC,
        "reviewer": None,
        "reviewed_at": None,
        "conclusion": None,
        "status": REVIEW_STATUS_PENDING,
        "review_question": "Confirm the figure against the regulation.",
    }
    entry.update(overrides)
    return entry


# --- the committed checklist -----------------------------------------------


def test_committed_checklist_is_valid() -> None:
    payload = load_checklist(CHECKLIST_PATH)

    assert validate_checklist(payload) == []


def test_committed_checklist_verifies_nothing() -> None:
    """The honest state: nothing here has been reviewed against a source.

    A fixture assertion is *supposed* to end up `SYNTHETIC_ONLY`. A single
    `VERIFIED_WITH_SOURCE` entry in this file would be a claim about regulatory
    truth that no qualified person has made.
    """
    payload = load_checklist(CHECKLIST_PATH)

    counts = summarize(payload)
    assert counts[REVIEW_STATUS_VERIFIED] == 0
    assert counts[REVIEW_STATUS_SYNTHETIC_ONLY] > 0
    for entry in payload["entries"]:
        assert entry["reviewer"] is None
        assert entry["reviewed_at"] is None
        assert entry["conclusion"] is None
        assert entry["authoritative_source"] is None


def test_committed_entries_are_located_and_classified() -> None:
    """Every row answers the questions the review actually turns on."""
    payload = load_checklist(CHECKLIST_PATH)

    for entry in payload["entries"]:
        assert entry["data_file"] in {"tests/evaluation/golden_set.jsonl", "offline/finetune_data.json"} or (
            entry["data_file"].startswith("tests/")
        ), entry["data_file"]
        assert (REPO_ROOT / entry["data_file"]).is_file(), entry["data_file"]
        assert entry["assertion_type"] in ASSERTION_TYPES
        assert entry["locator"].strip()
        assert entry["review_question"].strip()
        assert entry["license"] == LICENSE_UNKNOWN


def test_committed_checklist_covers_both_evaluation_and_training_fixtures() -> None:
    """Training output is the assertion most likely to leave the machine."""
    payload = load_checklist(CHECKLIST_PATH)
    files = {entry["data_file"] for entry in payload["entries"]}

    assert "offline/finetune_data.json" in files
    assert "tests/evaluation/golden_set.jsonl" in files


def test_no_claim_family_lost_its_review_state() -> None:
    """Coverage is asserted against reviewed code, not against the editable file.

    A row deleted from the JSON would still leave `--check` passing, which is
    exactly how a regulatory claim loses its review state without anyone
    deciding anything.
    """
    payload = load_checklist(CHECKLIST_PATH)
    committed = {entry["id"] for entry in payload["entries"]}

    assert committed == EXPECTED_CLAIM_FAMILIES


# --- promotion requires a human, a date and a versioned source --------------


def _promote(**overrides: Any) -> dict:
    kwargs: dict[str, Any] = {
        "reviewer": "qualified-reviewer",
        "reviewed_at": "2026-10-15",
        "conclusion": "Confirmed against the regulation text.",
        "authoritative_source": AUTHORITATIVE_SOURCE,
        "source_version": SOURCE_VERSION,
        "license": LICENSE_PUBLIC,
    }
    kwargs.update(overrides)
    return promote_to_verified(_entry(), **kwargs)


def test_promotion_records_every_credential_it_demands() -> None:
    promoted = _promote()

    assert promoted["status"] == REVIEW_STATUS_VERIFIED
    assert promoted["reviewer"] == "qualified-reviewer"
    assert promoted["reviewed_at"] == "2026-10-15"
    assert promoted["source_version"] == SOURCE_VERSION
    assert validate_entry(promoted) == []


@pytest.mark.parametrize(
    "missing",
    ["reviewer", "reviewed_at", "conclusion", "authoritative_source", "source_version"],
)
def test_promotion_refuses_when_a_credential_is_missing(missing: str) -> None:
    with pytest.raises(ReviewError):
        _promote(**{missing: ""})


def test_promotion_refuses_a_malformed_date() -> None:
    with pytest.raises(ReviewError):
        _promote(reviewed_at="15/10/2026")


def test_promotion_does_not_mutate_the_original_entry() -> None:
    original = _entry()

    _promote()

    assert original["status"] == REVIEW_STATUS_PENDING
    assert original["reviewer"] is None


# --- a fabricated placeholder can never be verified -------------------------


def test_declared_synthetic_placeholder_cannot_be_promoted() -> None:
    """The rule that stops fiction from being restated as regulation."""
    entry = _entry(is_explicit_synthetic_placeholder=True, status=REVIEW_STATUS_PENDING)

    with pytest.raises(ReviewError, match="fabricated text as real regulatory content"):
        promote_to_verified(
            entry,
            reviewer="qualified-reviewer",
            reviewed_at="2026-10-15",
            conclusion="Looks right.",
            authoritative_source=AUTHORITATIVE_SOURCE,
            source_version=SOURCE_VERSION,
            license=LICENSE_PUBLIC,
        )


def test_committed_entries_are_not_promotable() -> None:
    """Every entry actually in the file is declared synthetic, so none can move."""
    payload = load_checklist(CHECKLIST_PATH)

    for entry in payload["entries"]:
        with pytest.raises(ReviewError):
            promote_to_verified(
                entry,
                reviewer="qualified-reviewer",
                reviewed_at="2026-10-15",
                conclusion="Attempted promotion.",
                authoritative_source=AUTHORITATIVE_SOURCE,
                source_version=SOURCE_VERSION,
                license=LICENSE_PUBLIC,
            )


def test_a_synthetic_only_entry_cannot_be_promoted_even_if_the_flag_was_cleared() -> None:
    """Two independent guards: the state, and the flag."""
    entry = _entry(status=REVIEW_STATUS_SYNTHETIC_ONLY, is_explicit_synthetic_placeholder=False)

    with pytest.raises(ReviewError, match=REVIEW_STATUS_SYNTHETIC_ONLY):
        promote_to_verified(
            entry,
            reviewer="r",
            reviewed_at="2026-10-15",
            conclusion="c",
            authoritative_source=AUTHORITATIVE_SOURCE,
            source_version=SOURCE_VERSION,
            license=LICENSE_PUBLIC,
        )


# --- licensing is a separate question from correctness ----------------------


def test_unresolved_licence_cannot_be_promoted_to_verified() -> None:
    """ "Correct" and "free to redistribute" are different questions.

    Without this, a checklist entry could reach `VERIFIED_WITH_SOURCE` carrying
    `license: UNKNOWN` — the half-trustworthy state the project boundaries
    forbid. Public availability of a text is not a redistribution grant.
    """
    entry = _entry(license=LICENSE_UNKNOWN)

    with pytest.raises(ReviewError, match=LICENSE_UNKNOWN):
        promote_to_verified(
            entry,
            reviewer="r",
            reviewed_at="2026-10-15",
            conclusion="c",
            authoritative_source=AUTHORITATIVE_SOURCE,
            source_version=SOURCE_VERSION,
            license=LICENSE_UNKNOWN,
        )


def test_a_no_redistribution_entry_is_rejected_even_if_hand_written_as_verified() -> None:
    """Validation catches a hand-edited status, not just a programmatic one."""
    entry = _entry(status=REVIEW_STATUS_VERIFIED, reviewer="r", reviewed_at="2026-10-15", conclusion="c")
    entry["authoritative_source"] = AUTHORITATIVE_SOURCE
    entry["source_version"] = SOURCE_VERSION
    entry["license"] = LICENSE_NO_REDISTRIBUTION

    errors = validate_entry(entry)

    assert any(LICENSE_NO_REDISTRIBUTION in error for error in errors)


# --- unreviewed states cannot carry a reviewer ------------------------------


@pytest.mark.parametrize("status", [REVIEW_STATUS_PENDING, REVIEW_STATUS_SYNTHETIC_ONLY])
def test_an_unreviewed_state_must_not_name_a_reviewer(status: str) -> None:
    """Otherwise a name in the reviewer field reads as a review that happened."""
    errors = validate_entry(_entry(status=status, reviewer="someone-typed-a-name"))

    assert any("must not name a reviewer" in error for error in errors)


def test_every_state_is_reachable_only_from_a_place_it_belongs() -> None:
    """A typo in the vocabulary must fail, not silently become 'pending'."""
    assert validate_entry(_entry(status="VERIFIED")) != []
    assert validate_entry(_entry(assertion_type="SOMETHING")) != []
    assert validate_entry(_entry(license="maybe")) != []
    assert REVIEW_STATUS_BLOCKED_BY_LICENSE in REVIEW_STATUSES
    assert REVIEW_STATUS_REJECTED in REVIEW_STATUSES


def test_a_rejected_entry_cannot_be_re_entered() -> None:
    with pytest.raises(ReviewError, match="terminal"):
        promote_to_verified(
            _entry(status=REVIEW_STATUS_REJECTED, is_explicit_synthetic_placeholder=None),
            reviewer="r",
            reviewed_at="2026-10-15",
            conclusion="c",
            authoritative_source=AUTHORITATIVE_SOURCE,
            source_version=SOURCE_VERSION,
            license=LICENSE_PUBLIC,
        )


# --- a hand-edited file must meet the same bar as the promotion function -----


def test_a_hand_written_verified_entry_with_an_unknown_licence_is_rejected() -> None:
    """The JSON is directly editable, so validation must repeat the licence rule.

    Otherwise the CI gate advertises a restriction that a manual edit walks
    straight past, and a "verified" claim whose redistribution terms were never
    resolved passes the check meant to prevent exactly that.
    """
    entry = _entry(
        status=REVIEW_STATUS_VERIFIED,
        reviewer="r",
        reviewed_at="2026-10-15",
        conclusion="c",
        authoritative_source=AUTHORITATIVE_SOURCE,
        source_version=SOURCE_VERSION,
        license=LICENSE_UNKNOWN,
    )

    errors = validate_entry(entry)

    assert any("established license" in error for error in errors)


def test_a_hand_written_verified_entry_needs_a_real_calendar_date() -> None:
    """A regex would accept 2026-99-99; the audit trail must not be fictional."""
    entry = _entry(
        status=REVIEW_STATUS_VERIFIED,
        reviewer="r",
        reviewed_at="2026-99-99",
        conclusion="c",
        authoritative_source=AUTHORITATIVE_SOURCE,
        source_version=SOURCE_VERSION,
        license=LICENSE_PUBLIC,
    )

    errors = validate_entry(entry)

    assert any("reviewed_at" in error for error in errors)


def test_promotion_refuses_an_impossible_date() -> None:
    with pytest.raises(ReviewError, match="reviewed_at"):
        _promote(reviewed_at="2026-02-30")


def test_promotion_accepts_a_real_leap_day() -> None:
    """The date check must not be so strict it rejects a genuine date."""
    promoted = _promote(reviewed_at="2024-02-29")

    assert promoted["reviewed_at"] == "2024-02-29"


# --- rendering --------------------------------------------------------------


def test_markdown_shows_every_state_so_none_is_invisible() -> None:
    markdown = render_markdown(load_checklist(CHECKLIST_PATH))

    for status in REVIEW_STATUSES:
        assert f"`{status}`" in markdown


def test_markdown_states_that_no_state_is_asserted_by_tooling() -> None:
    markdown = render_markdown(load_checklist(CHECKLIST_PATH))

    assert "promote_to_verified()" in markdown
    assert "refuses entries declared to be synthetic placeholders" in markdown


def test_markdown_is_valid_json_free_of_broken_links() -> None:
    """The boundary guard parses links; a stray `](...)` here would fail CI."""
    markdown = render_markdown(load_checklist(CHECKLIST_PATH))

    assert "](" not in markdown
    assert "http" not in markdown


# --- the file on disk is the thing being validated --------------------------


def test_load_failure_is_an_error_not_an_empty_checklist() -> None:
    """An unreadable tracker must fail loudly, never read as 'nothing pending'."""
    with pytest.raises(ReviewError):
        load_checklist(CHECKLIST_PATH.parent / "does-not-exist.json")


def test_a_checklist_with_no_entries_is_invalid() -> None:
    """An empty tracker would report zero outstanding work and look like done."""
    errors = validate_checklist({"schema_version": "regulatory-review/v1", "entries": []})

    assert errors


def test_duplicate_ids_are_rejected() -> None:
    payload = {"schema_version": "regulatory-review/v1", "entries": [_entry(), _entry()]}

    errors = validate_checklist(payload)

    assert any("duplicate" in error for error in errors)


def test_shipped_checklist_is_json_serialisable_and_stable() -> None:
    """Guards against a hand edit that breaks the round trip."""
    raw = CHECKLIST_PATH.read_text(encoding="utf-8")

    assert json.loads(raw) == load_checklist(CHECKLIST_PATH)
    assert validate_checklist(json.loads(raw)) == []
