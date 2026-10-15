#!/usr/bin/env python3
"""Track the professional review state of regulatory assertions in this repo.

The problem this solves
-----------------------
``docs/finalization/DATA_REVIEW_BACKLOG.md`` already names the *categories* of
regulatory claim that need a qualified reviewer: heavy-metal limits, maximum
allowable concentrations, standard numbers, filing regimes. Those rows are prose,
which has three failure modes in practice:

1. There is no state per assertion, only a tick-box per topic, so "partly
   reviewed" and "not reviewed" look the same.
2. Nothing records *who* decided, *when*, or *against which source and version* —
   so an item cannot later be audited, and a second reviewer cannot tell whether
   it is looking at a fresh judgement.
3. Nothing prevents a status flip. A tracker whose fields are all editable has no
   rule about which edit is legitimate, so "reviewed" can be typed by anyone,
   including a tool that never read a regulation.

This module makes the third failure structurally impossible rather than socially
discouraged. :func:`promote_to_verified` is the *only* path to
:data:`REVIEW_STATUS_VERIFIED_WITH_SOURCE`, and it demands a named reviewer, a
date, a conclusion, and a citable source *with a version*. It additionally
refuses to verify an entry that is already declared a synthetic placeholder: the
fixtures are fabricated on purpose, so "verifying" one would convert a
knowingly-fictional value into a claimed-real one, which is the opposite of
reviewing it.

What it does not do
-------------------
It never reads a regulation, never resolves a source, and never moves an item
forward on its own. Every entry ships in a pending or synthetic-only state, and
only a qualified human using this module can change that. Asserting a limit is
not something this file can help with.

Usage
-----
    python3 scripts/validation/regulatory_review.py --check
    python3 scripts/validation/regulatory_review.py --markdown

Exit codes
----------
    0  the checklist is well-formed
    1  at least one entry violates the schema or the promotion rules
    2  the checklist file could not be read
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_CHECKLIST = REPO_ROOT / "docs" / "finalization" / "REGULATORY_REVIEW_CHECKLIST.json"

CHECKLIST_SCHEMA_VERSION = "regulatory-review/v1"

# --- review states ----------------------------------------------------------
#
# Five states, ordered by what they permit rather than by how good they sound.
# The ordering matters: only two of the five are ever reached by human action,
# and the terminal ones cannot be left by re-running a script.

#: Nobody qualified has judged this yet. The default, and the only state a new
#: assertion may be created in.
REVIEW_STATUS_PENDING = "PENDING_REVIEW"
#: A named reviewer confirmed it against a citable source, at a named version.
#: Reachable only through :func:`promote_to_verified`.
REVIEW_STATUS_VERIFIED = "VERIFIED_WITH_SOURCE"
#: Confirmed to be a deliberately fabricated placeholder. Terminal: the correct
#: end state for this repository's fixtures, and never a step on the way to real
#: regulatory content.
REVIEW_STATUS_SYNTHETIC_ONLY = "SYNTHETIC_ONLY"
#: Confirmed wrong and withdrawn from use.
REVIEW_STATUS_REJECTED = "REJECTED"
#: Content may not be used or redistributed because its licence is unresolved.
#: Blocking on licence is a real state and is not the same as pending review:
#: reviewing it will not unblock it.
REVIEW_STATUS_BLOCKED_BY_LICENSE = "BLOCKED_BY_LICENSE"

REVIEW_STATUSES = (
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_VERIFIED,
    REVIEW_STATUS_SYNTHETIC_ONLY,
    REVIEW_STATUS_REJECTED,
    REVIEW_STATUS_BLOCKED_BY_LICENSE,
)

#: States that record a human decision and cannot be re-entered from another
#: state by this module.
TERMINAL_STATUSES = frozenset({REVIEW_STATUS_VERIFIED, REVIEW_STATUS_REJECTED, REVIEW_STATUS_BLOCKED_BY_LICENSE})

#: States an assertion may sit in before a reviewer has looked at it.
UNREVIEWED_STATUSES = frozenset({REVIEW_STATUS_PENDING, REVIEW_STATUS_SYNTHETIC_ONLY})

# --- assertion vocabulary ---------------------------------------------------

#: The regulatory shapes that make a fixture row read as authority. Kept in
#: step with ``DATA_MANIFEST.json``'s ``regulatory_shaped_fields`` idea: a claim
#: is reviewable because of its *shape*, not because of the topic it is about.
ASSERTION_TYPE_REGULATION_NAME = "REGULATION_NAME"
ASSERTION_TYPE_STANDARD_NUMBER = "STANDARD_NUMBER"
ASSERTION_TYPE_CONCENTRATION = "CONCENTRATION"
ASSERTION_TYPE_LIMIT_VALUE = "LIMIT_VALUE"
ASSERTION_TYPE_PROHIBITION = "PROHIBITION_OR_RESTRICTION"

ASSERTION_TYPES = (
    ASSERTION_TYPE_REGULATION_NAME,
    ASSERTION_TYPE_STANDARD_NUMBER,
    ASSERTION_TYPE_CONCENTRATION,
    ASSERTION_TYPE_LIMIT_VALUE,
    ASSERTION_TYPE_PROHIBITION,
)

#: Every entry must carry these. A missing key is an error, not a default: an
#: absent ``reviewer`` and an empty ``reviewer`` must not be distinguishable
#: between "nobody reviewed this" and "this field was never considered".
REQUIRED_FIELDS = (
    "id",
    "data_file",
    "locator",
    "regulatory_shaped_field",
    "assertion_type",
    "is_explicit_synthetic_placeholder",
    "standalone_misreading_risk",
    "authoritative_source",
    "source_version",
    "license",
    "reviewer",
    "reviewed_at",
    "conclusion",
    "status",
    "review_question",
)

#: The clauses that make ``VERIFIED_WITH_SOURCE`` mean something. An entry in
#: that state without a named reviewer, a date, a conclusion and a versioned
#: source is not a reviewed assertion; it is an unlabelled one.
REQUIRED_FOR_VERIFICATION = (
    "reviewer",
    "reviewed_at",
    "conclusion",
    "authoritative_source",
    "source_version",
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Licence states. ``UNKNOWN`` is the default and is a blocker, not a formality:
#: public availability of a standard text is not a redistribution grant.
LICENSE_UNKNOWN = "UNKNOWN"
LICENSE_PUBLIC = "PUBLIC_USE_OK"
LICENSE_ATTRIBUTION_REQUIRED = "ATTRIBUTION_REQUIRED"
LICENSE_NO_REDISTRIBUTION = "NO_REDISTRIBUTION"

LICENSE_STATES = (LICENSE_UNKNOWN, LICENSE_PUBLIC, LICENSE_ATTRIBUTION_REQUIRED, LICENSE_NO_REDISTRIBUTION)


class ReviewError(ValueError):
    """Raised when a review transition is not permitted."""


def _is_filled(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def load_checklist(path: str | Path = DEFAULT_CHECKLIST) -> dict[str, Any]:
    """Read the checklist file, raising :class:`ReviewError` when unreadable."""
    try:
        decoded = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReviewError(f"{path}: cannot be read ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise ReviewError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(decoded, dict):
        raise ReviewError(f"{path}: the checklist must be a JSON object")
    return decoded


def validate_entry(entry: Mapping[str, Any]) -> list[str]:
    """Return every schema and transition-rule violation for one entry."""
    errors: list[str] = []
    entry_id = entry.get("id") or "<no id>"

    for field in REQUIRED_FIELDS:
        if field not in entry:
            errors.append(f"{entry_id}: missing required field {field!r}")

    if not _is_filled(entry.get("id")) or not _ID_RE.match(str(entry.get("id"))):
        errors.append(f"{entry_id}: id must be lowercase [a-z0-9_-], 3-64 chars")

    for field in ("data_file", "locator", "regulatory_shaped_field", "review_question"):
        if not _is_filled(entry.get(field)):
            errors.append(f"{entry_id}: {field} must be a non-empty string")

    assertion_type = entry.get("assertion_type")
    if assertion_type not in ASSERTION_TYPES:
        errors.append(f"{entry_id}: assertion_type {assertion_type!r} is not one of {list(ASSERTION_TYPES)}")

    status = entry.get("status")
    if status not in REVIEW_STATUSES:
        errors.append(f"{entry_id}: status {status!r} is not one of {list(REVIEW_STATUSES)}")

    license_state = entry.get("license")
    if license_state not in LICENSE_STATES:
        errors.append(f"{entry_id}: license {license_state!r} is not one of {list(LICENSE_STATES)}")

    # Three-valued on purpose: True = deliberately fabricated, False = asserted as
    # real, None = nobody has determined which. Collapsing None into False would
    # make "unexamined" read as "confirmed real".
    if "is_explicit_synthetic_placeholder" in entry and not isinstance(
        entry["is_explicit_synthetic_placeholder"], bool | type(None)
    ):
        errors.append(f"{entry_id}: is_explicit_synthetic_placeholder must be true, false or null")

    reviewed_at = entry.get("reviewed_at")
    if reviewed_at is not None and not (_is_filled(reviewed_at) and _DATE_RE.match(str(reviewed_at))):
        errors.append(f"{entry_id}: reviewed_at must be null or an ISO YYYY-MM-DD date")

    if status == REVIEW_STATUS_VERIFIED:
        for field in REQUIRED_FOR_VERIFICATION:
            if not _is_filled(entry.get(field)):
                errors.append(f"{entry_id}: status {REVIEW_STATUS_VERIFIED} requires {field!r}")
        if entry.get("is_explicit_synthetic_placeholder") is True:
            errors.append(
                f"{entry_id}: a declared synthetic placeholder cannot be {REVIEW_STATUS_VERIFIED}; "
                "verifying fabricated text would restate it as real regulatory content"
            )
        if license_state == LICENSE_NO_REDISTRIBUTION:
            errors.append(f"{entry_id}: status {REVIEW_STATUS_VERIFIED} conflicts with license NO_REDISTRIBUTION")

    if status in UNREVIEWED_STATUSES and _is_filled(entry.get("reviewer")):
        errors.append(
            f"{entry_id}: status {status} must not name a reviewer; "
            f"{REVIEW_STATUS_VERIFIED} is the only state in which one may be recorded"
        )

    return errors


def validate_checklist(payload: Mapping[str, Any]) -> list[str]:
    """Validate the whole checklist, including cross-entry invariants."""
    errors: list[str] = []
    if payload.get("schema_version") != CHECKLIST_SCHEMA_VERSION:
        errors.append(
            f"schema_version {payload.get('schema_version')!r} is not {CHECKLIST_SCHEMA_VERSION!r}; "
            "a changed vocabulary needs a deliberate version bump, not a silent edit"
        )

    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("the checklist declares no entries; it must list what is awaiting review")
        return errors

    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            errors.append(f"entry {index}: each entry must be an object")
            continue
        errors.extend(validate_entry(entry))
        entry_id = entry.get("id")
        if _is_filled(entry_id):
            if entry_id in seen:
                errors.append(f"{entry_id}: duplicate entry id")
            seen.add(str(entry_id))

    return errors


def promote_to_verified(
    entry: Mapping[str, Any],
    *,
    reviewer: str,
    reviewed_at: str,
    conclusion: str,
    authoritative_source: str,
    source_version: str,
    license: str = LICENSE_UNKNOWN,
) -> dict[str, Any]:
    """Return a copy of ``entry`` promoted to :data:`REVIEW_STATUS_VERIFIED`.

    This is the only route to that state and it refuses every shortcut that would
    make the state meaningless:

    * a declared synthetic placeholder cannot be promoted at all;
    * a terminal state cannot be re-entered;
    * an unresolved or non-redistributable licence cannot be promoted, because
      "verified correct" and "free to redistribute" are different questions and
      answering one does not answer the other;
    * reviewer, date and conclusion must all be present, so no field is filled
      in on the reviewer's behalf.

    ``conclusion`` is the reviewer's own words, not a summary written for them.
    """
    errors = validate_entry(entry)
    current = entry.get("status")
    if errors:
        raise ReviewError(f"{entry.get('id')}: existing entry is not valid to promote ({'; '.join(errors)})")

    entry_id = str(entry.get("id"))
    if current in TERMINAL_STATUSES:
        raise ReviewError(f"{entry_id}: already in terminal state {current}")
    if current == REVIEW_STATUS_SYNTHETIC_ONLY:
        raise ReviewError(
            f"{entry_id}: is {REVIEW_STATUS_SYNTHETIC_ONLY}; a deliberately fabricated placeholder "
            f"cannot be promoted to {REVIEW_STATUS_VERIFIED}"
        )
    if entry.get("is_explicit_synthetic_placeholder") is True:
        raise ReviewError(
            f"{entry_id}: is_explicit_synthetic_placeholder is true; verifying it would restate "
            "fabricated text as real regulatory content"
        )
    if license == LICENSE_NO_REDISTRIBUTION:
        raise ReviewError(
            f"{entry_id}: license is {LICENSE_NO_REDISTRIBUTION}; resolve the licence before recording a verified state"
        )
    if license == LICENSE_UNKNOWN:
        # The strict reading, and the one the project boundaries require: an
        # entry with an unresolved licence may not be presented as sourced
        # regulatory material, because "correct" and "free to quote" are
        # different questions and a checklist that answers only the first still
        # produces a half-trustworthy corpus.
        raise ReviewError(
            f"{entry_id}: license is {LICENSE_UNKNOWN}; a verified entry must record a licence "
            f"the reviewer actually established (one of {[s for s in LICENSE_STATES if s != LICENSE_UNKNOWN]})"
        )
    if license not in LICENSE_STATES:
        raise ReviewError(f"{entry_id}: license {license!r} is not one of {list(LICENSE_STATES)}")
    if not _is_filled(reviewer):
        raise ReviewError("a verification requires a named reviewer")
    if not _DATE_RE.match(str(reviewed_at or "")):
        raise ReviewError("a verification requires an ISO reviewed_at date")
    if not _is_filled(conclusion):
        raise ReviewError("a verification requires the reviewer's own conclusion")
    if not _is_filled(authoritative_source):
        raise ReviewError("a verification requires a citable authoritative source")
    if not _is_filled(source_version):
        raise ReviewError("a verification requires the source version; regulations are versioned")

    promoted = deepcopy(dict(entry))
    promoted.update(
        {
            "status": REVIEW_STATUS_VERIFIED,
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "conclusion": conclusion,
            "authoritative_source": authoritative_source,
            "source_version": source_version,
            "license": license,
        }
    )
    remaining = validate_entry(promoted)
    if remaining:
        raise ReviewError(f"{entry_id}: the promoted entry is still invalid ({'; '.join(remaining)})")
    return promoted


def summarize(payload: Mapping[str, Any]) -> dict[str, int]:
    """Count entries per review state."""
    counts = dict.fromkeys(REVIEW_STATUSES, 0)
    for entry in payload.get("entries") or []:
        if isinstance(entry, Mapping) and entry.get("status") in counts:
            counts[str(entry["status"])] += 1
    return counts


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(payload: Mapping[str, Any]) -> str:
    """Render the checklist as the human-facing review table.

    States appear as a summary line before the table so a reader learns how much
    of this is still outstanding before reading any single row.
    """
    counts = summarize(payload)
    entries = [entry for entry in (payload.get("entries") or []) if isinstance(entry, Mapping)]

    lines = [
        "### 法规断言专业复核清单 / Regulatory assertion review checklist",
        "",
        f"Machine-readable source: `{DEFAULT_CHECKLIST.relative_to(REPO_ROOT).as_posix()}` "
        f"(schema `{CHECKLIST_SCHEMA_VERSION}`). Regenerate with "
        "`python3 scripts/validation/regulatory_review.py --markdown`.",
        "",
        "| 状态 / state | 条目数 |",
        "|---|---:|",
    ]
    lines += [f"| `{status}` | {counts[status]} |" for status in REVIEW_STATUSES]
    lines += [
        "",
        "None of these states is asserted by this repository's tooling. "
        f"`{REVIEW_STATUS_VERIFIED}` is reachable only through "
        "`regulatory_review.promote_to_verified()`, which requires a named reviewer, a date, "
        "a conclusion and a versioned citable source — and refuses entries declared to be "
        "synthetic placeholders.",
        "",
        "| id | data file / locator | field | assertion | synthetic placeholder | risk | status | reviewer | source · version | licence |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in entries:
        lines.append(
            "| `{id}` | `{data_file}`<br>{locator} | `{field}` | `{assertion}` | {placeholder} | {risk} "
            "| `{status}` | {reviewer} | {source} · {version} | `{license}` |".format(
                id=_cell(entry.get("id")),
                data_file=_cell(entry.get("data_file")),
                locator=_cell(entry.get("locator")),
                field=_cell(entry.get("regulatory_shaped_field")),
                assertion=_cell(entry.get("assertion_type")),
                placeholder=_cell(entry.get("is_explicit_synthetic_placeholder")),
                risk=_cell(entry.get("standalone_misreading_risk")),
                status=_cell(entry.get("status")),
                reviewer=_cell(entry.get("reviewer")),
                source=_cell(entry.get("authoritative_source")),
                version=_cell(entry.get("source_version")),
                license=_cell(entry.get("license")),
            )
        )
    lines += ["", "每条目的待核验问题（`review_question`）：", ""]
    lines += [f"- `{_cell(entry.get('id'))}` — {_cell(entry.get('review_question'))}" for entry in entries]
    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checklist", default=str(DEFAULT_CHECKLIST), help="checklist JSON to read")
    parser.add_argument("--check", action="store_true", help="validate the checklist and exit")
    parser.add_argument("--markdown", action="store_true", help="render the checklist as Markdown")
    args = parser.parse_args(argv)

    try:
        payload = load_checklist(args.checklist)
    except ReviewError as exc:
        print(f"checklist error: {exc}", file=sys.stderr)
        return 2

    errors = validate_checklist(payload)
    if errors:
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        print(f"{len(errors)} problem(s) in {args.checklist}", file=sys.stderr)
        return 1

    if args.markdown:
        print(render_markdown(payload))
    if args.check or not args.markdown:
        counts = summarize(payload)
        print(
            f"checklist ok: {len(payload.get('entries') or [])} entries — "
            + ", ".join(f"{k}={v}" for k, v in counts.items())
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
