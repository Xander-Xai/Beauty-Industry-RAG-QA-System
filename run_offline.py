"""Offline knowledge base CLI: indexing, ingestion, rebuild, sealing, feedback."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

from common.audit import (
    ACTION_EPOCH_SEAL,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    audit_event,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

#: Epoch sealing is an operator CLI action, not an authenticated API call. The
#: actor is recorded as a CLI principal; the OS user is kept in metadata so the
#: record is still attributable on a shared host.
CLI_ACTOR_ID = "cli_operator"


def _operator_name() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or "unknown"


def _quarantined_sources(exc) -> list[str]:
    """Return the source ids a failed validation refused, without string parsing."""
    report = getattr(exc, "report", None)
    return list(getattr(report, "quarantined_sources", []) or [])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline knowledge base tools")
    parser.add_argument("--mode", choices=["rewrite-feedback"], help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command")

    create_index = subparsers.add_parser("create-index", help="Ensure Qdrant collections and the ES index exist")
    create_index.add_argument("--recreate", action="store_true", help="Destructively recreate indexes")
    create_index.add_argument("--yes", action="store_true", help="Confirm destructive recreation")

    for name, help_text in (
        ("ingest", "Ingest one source document/image into an epoch"),
        ("ingest-text", "Legacy alias for TXT-only ingestion"),
    ):
        ingest = subparsers.add_parser(name, help=help_text)
        ingest.add_argument("source", help="Path to a source file")
        ingest.add_argument("--role-mask", type=int, required=True)
        ingest.add_argument("--dept-mask", type=int, required=True)
        ingest.add_argument("--epoch", required=True)
        ingest.add_argument("--source-id", help="Stable source id when SOURCE is outside data_dir")
        # Optional: omitting it resolves the same path rules discovery uses, so an
        # import path is classified UNTRUSTED without the operator restating it.
        ingest.add_argument(
            "--source-trust",
            choices=["MANAGED_INTERNAL", "UNTRUSTED"],
            help="Provenance claim for this source (default: resolved from source_trust.rules)",
        )

    incremental = subparsers.add_parser("incremental-build", help="Build a new epoch from changes")
    incremental.add_argument("--from-epoch", default=None)
    incremental.add_argument("--to-epoch", default=None)
    incremental.add_argument("--seal", action="store_true")

    rebuild = subparsers.add_parser("full-rebuild", help="Rebuild every source into a new epoch")
    rebuild.add_argument("--epoch", required=True)
    rebuild.add_argument("--seal", action="store_true")

    seal = subparsers.add_parser("seal-epoch", help="Validate and seal an epoch snapshot")
    seal.add_argument("--epoch", required=True)
    seal.add_argument("--skip-validation", action="store_true")

    # Trust levels are spelled out here so `--help` stays free of offline imports;
    # tests assert they match offline.source_trust.
    review = subparsers.add_parser(
        "review-source",
        help="Record an explicit trust decision for an imported source, or list the quarantine queue",
    )
    review.add_argument("source", nargs="?", help="Path to the source file under review")
    review.add_argument("--source-id", help="Stable source id when SOURCE is outside data_dir")
    decision = review.add_mutually_exclusive_group()
    decision.add_argument("--approve", action="store_true", help="Admit these exact bytes to activatable snapshots")
    decision.add_argument("--reject", action="store_true", help="Refuse this source; it will not be ingested at all")
    decision.add_argument("--list", action="store_true", dest="list_quarantined", help="List quarantined sources")
    review.add_argument("--actor", help="Reviewer identity recorded with the decision (required to approve/reject)")
    review.add_argument("--note", default="", help="Why this decision was made")
    review.add_argument(
        "--store-path", default=None, help="Approval ledger (default: source_trust.approval_store_path)"
    )

    # Candidate review statuses are spelled out here so `--help` stays free of
    # offline imports; tests assert they match offline.regression_candidates.
    regression = subparsers.add_parser(
        "export-regression-candidates",
        help="Build regression candidates from reviewed negative feedback and export the approved dataset",
    )
    regression.add_argument("--store-path", default=None, help="Feedback store (default: offline.feedback.store_path)")
    regression.add_argument(
        "--output-dir", default=None, help="Export directory (default: offline.feedback.output_dir)"
    )
    regression.add_argument(
        "--status",
        choices=["PENDING_REVIEW", "accepted", "rejected", "all"],
        default="PENDING_REVIEW",
        help="Candidate review status to export for review (default: PENDING_REVIEW)",
    )
    return parser


def _handle_create_index(args) -> int:
    from offline.snapshot_builder import configured_snapshot_builder

    if args.recreate and not args.yes:
        logger.error("create-index --recreate is destructive and requires --yes")
        return 2

    builder = configured_snapshot_builder()
    for writer in (builder.text_writer, builder.image_writer):
        if args.recreate and writer.client.collection_exists(writer.collection_name):
            writer.client.delete_collection(writer.collection_name)
            logger.warning("Deleted Qdrant collection %s", writer.collection_name)
        writer.ensure_collection()
    if builder.es_writer is not None:
        builder.es_writer.ensure_index(recreate=args.recreate, confirm=args.yes)
    action = "recreated" if args.recreate else "verified"
    logger.info("Indexes %s (Qdrant text, Qdrant image, Elasticsearch).", action)
    return 0


def _handle_ingest(args) -> int:
    from pathlib import Path

    from offline.snapshot_builder import IngestionSource, classify_source, configured_snapshot_builder
    from offline.source_trust import normalize_source_trust, resolve_source_trust

    document_type = classify_source(args.source)
    source_id = args.source_id
    from common.config import get_config_dict

    config = get_config_dict()
    data_dir = Path(config.get("knowledge_base", {}).get("data_dir", "./data")).resolve()
    if not source_id:
        try:
            source_id = Path(args.source).resolve().relative_to(data_dir).as_posix()
        except ValueError:
            logger.error("source is outside knowledge_base.data_dir; provide --source-id")
            return 2
    # Explicit declaration wins; otherwise the same path rules discovery applies,
    # so a single-source ingest of an import path is quarantined by default too.
    if args.source_trust:
        source_trust = normalize_source_trust(args.source_trust)
    else:
        source_trust = resolve_source_trust(source_id, config.get("source_trust", {}) or {})
    source = IngestionSource(
        source_id=source_id,
        path=args.source,
        document_type=document_type,
        role_mask=args.role_mask,
        dept_mask=args.dept_mask,
        source_trust=source_trust,
    )
    builder = configured_snapshot_builder()
    chunks, images = builder.ingest_source(source, args.epoch)
    logger.info("Ingested %d chunks and %d images into epoch %s", chunks, images, args.epoch)
    logger.info(
        "Source trust: %s (activation eligibility: %s)",
        source_trust,
        "sealed-and-activatable" if source_trust == "MANAGED_INTERNAL" else "quarantined until an explicit review",
    )
    return 0


def _handle_ingest_text(args) -> int:
    from common.config import get_config_dict
    from offline.scheduler import OfflineScheduler
    from offline.source_trust import file_content_hash, normalize_source_trust, resolve_source_trust
    from offline.text_ingestion import configured_text_ingestion_service

    config = get_config_dict()
    source_id = args.source_id or args.source
    # The legacy TXT slice resolves trust exactly like the full ingest path: an
    # explicit --source-trust wins, otherwise the configured path rules classify
    # it. Declaring managed provenance unconditionally would make this alias a
    # direct bypass of the quarantine gate — a TXT import is quarantined by
    # default too, and an approval recorded in the ledger is honored here.
    if args.source_trust:
        source_trust = normalize_source_trust(args.source_trust)
    else:
        source_trust = resolve_source_trust(source_id, config.get("source_trust", {}) or {})
    scheduler = OfflineScheduler(config=config)
    try:
        provenance = scheduler.trust_registry.resolve(
            source_id,
            declared_trust=source_trust,
            content_hash=file_content_hash(args.source),
        ).to_payload()
    finally:
        scheduler.trust_registry.close()
    chunks = configured_text_ingestion_service().ingest(
        args.source,
        role_mask=args.role_mask,
        dept_mask=args.dept_mask,
        doc_version_epoch=args.epoch,
        source_id=args.source_id,
        provenance=provenance,
    )
    logger.info(
        "Ingested %d text chunks (source trust: %s)",
        len(chunks),
        "activatable" if provenance["trust_class"] == "MANAGED_INTERNAL" else "quarantined until review",
    )
    return 0


def _handle_incremental_build(args) -> int:
    from offline.scheduler import OfflineScheduler

    scheduler = OfflineScheduler()
    result = scheduler.run_incremental_cycle(from_epoch=args.from_epoch, to_epoch=args.to_epoch, seal=args.seal)
    logger.info("Incremental build complete: %s", result)
    return 0


def _handle_full_rebuild(args) -> int:
    from offline.scheduler import OfflineScheduler

    scheduler = OfflineScheduler()
    result = scheduler.run_full_rebuild_cycle(epoch=args.epoch, seal=args.seal)
    logger.info("Full rebuild complete: %s", result)
    return 0


def _handle_seal_epoch(args) -> int:
    """Seal a knowledge epoch, auditing both the outcome and how it was sealed.

    Sealing is a release action: after it the epoch is immutable, and activation
    is a separate manual step. A seal performed with ``--skip-validation`` is
    recorded distinctly because it bypasses the snapshot check, and a seal refused
    by the ingestion trust gate records each quarantined source separately.
    """
    from offline.snapshot_builder import configured_snapshot_builder
    from offline.source_trust import audit_activation_refused

    builder = configured_snapshot_builder()
    if args.skip_validation:
        builder.seal(args.epoch)
        logger.warning(
            "DANGER: sealed epoch %s WITHOUT snapshot validation (--skip-validation). "
            "Prefer the normal validate-then-seal path.",
            args.epoch,
        )
        audit_event(
            action=ACTION_EPOCH_SEAL,
            outcome=OUTCOME_SUCCESS,
            actor_id=CLI_ACTOR_ID,
            resource_type="knowledge_epoch",
            resource_id=args.epoch,
            metadata={"skip_validation": True, "validated": False, "operator": _operator_name()},
        )
        return 0
    try:
        builder.seal_epoch(args.epoch, validate=True)
    except Exception as exc:
        # Validation failure must never be a silent non-event: it is the signal
        # that the previous sealed epoch stays active.
        audit_event(
            action=ACTION_EPOCH_SEAL,
            outcome=OUTCOME_FAILED,
            actor_id=CLI_ACTOR_ID,
            resource_type="knowledge_epoch",
            resource_id=args.epoch,
            reason="snapshot validation failed; epoch not sealed",
            metadata={
                "skip_validation": False,
                "error_type": type(exc).__name__,
                "operator": _operator_name(),
            },
        )
        # Attribute the refusal per quarantined source. The report carries them
        # as structured ids, so this never parses an error string, and only the
        # bounded trust class reaches the audit record — never document content.
        for source_id in _quarantined_sources(exc):
            audit_activation_refused(
                source_id=source_id,
                trust_class="UNTRUSTED",
                reason="source has no explicit review approval; epoch is not activatable",
                actor_id=CLI_ACTOR_ID,
                epoch=args.epoch,
            )
        raise
    logger.info(
        "Validated and sealed epoch %s (Qdrant text/image + Elasticsearch); "
        "switch knowledge_version_epoch only after verification.",
        args.epoch,
    )
    audit_event(
        action=ACTION_EPOCH_SEAL,
        outcome=OUTCOME_SUCCESS,
        actor_id=CLI_ACTOR_ID,
        resource_type="knowledge_epoch",
        resource_id=args.epoch,
        metadata={"skip_validation": False, "validated": True, "operator": _operator_name()},
    )
    return 0


def _handle_review_source(args) -> int:
    """Record or list explicit source trust decisions.

    This is the *only* path that moves a source out of quarantine, and it is
    deliberately not automatic: it needs an explicit ``--approve``/``--reject``
    and a named ``--actor``, and it binds the decision to the content hash of
    the bytes on disk. Approving a file therefore does not approve a later edit
    of that file, and a review that omits the actor fails instead of defaulting
    to approval. Both outcomes are audited.
    """

    from common.config import get_config_dict
    from offline.scheduler import OfflineScheduler
    from offline.source_trust import (
        TrustRegistry,
    )

    config = get_config_dict()
    # One ledger for reading and writing, so --list and the decision below can
    # never disagree about which approvals exist.
    scheduler = OfflineScheduler(config=config)
    registry = TrustRegistry(args.store_path) if args.store_path else scheduler.trust_registry
    try:
        return _review_source(args, config, scheduler, registry)
    finally:
        registry.close()


def _review_source(args, config: dict, scheduler, registry) -> int:
    from pathlib import Path

    from offline.source_trust import (
        APPROVAL_APPROVED,
        APPROVAL_REJECTED,
        TRUST_MANAGED_INTERNAL,
        SourceTrustError,
        file_content_hash,
    )

    if args.list_quarantined:
        queue = registry.quarantined_sources(scheduler.discover_sources())
        if not queue:
            logger.info("No quarantined sources: every discovered source is activatable.")
            return 0
        logger.warning("%d quarantined source(s) cannot be sealed or activated:", len(queue))
        for entry in queue:
            logger.warning(
                "  %s  [%s]  content_hash=%s  — %s",
                entry["source_id"],
                entry["trust_class"],
                entry["content_hash"][:16],
                entry["reason"],
            )
        return 0

    if not args.source:
        logger.error("review-source needs a SOURCE path, or --list")
        return 2
    if not (args.approve or args.reject):
        logger.error("review-source needs exactly one of --approve, --reject or --list")
        return 2

    data_dir = Path(config.get("knowledge_base", {}).get("data_dir", "./data")).resolve()
    source_id = args.source_id
    if not source_id:
        try:
            source_id = Path(args.source).resolve().relative_to(data_dir).as_posix()
        except ValueError:
            logger.error("source is outside knowledge_base.data_dir; provide --source-id")
            return 2

    matching = [source for source in scheduler.discover_sources() if source.source_id == source_id]
    if matching:
        source_path = matching[0].path
        source_trust = matching[0].source_trust
    elif args.source_id:
        # An out-of-tree import is staged via `ingest SOURCE --source-id X`, so it
        # is deliberately absent from discovery. The explicit id plus the file on
        # disk is enough to review it, and trust is resolved by the same path
        # rules the ingest path uses — otherwise such a source could be staged
        # but never approved.
        source_path = args.source
        source_trust = scheduler.resolve_source_trust(source_id)
    else:
        logger.error(
            "source %s is not a discovered ingestion source (unsupported type, filtered by "
            "knowledge_base.supported_extensions, or missing from data_dir)",
            source_id,
        )
        return 2

    if source_trust == TRUST_MANAGED_INTERNAL:
        # A managed source is NOT_REQUIRED by contract, so a stored decision could
        # only be ignored by resolve(). Refuse it rather than audit a decision the
        # pipeline will not honor.
        logger.error(
            "source %s resolves to MANAGED_INTERNAL; managed sources need no review decision",
            source_id,
        )
        return 2

    try:
        decision = registry.decide(
            source_id,
            content_hash=file_content_hash(source_path),
            approval_status=APPROVAL_APPROVED if args.approve else APPROVAL_REJECTED,
            actor=args.actor or "",
            note=args.note,
        )
    except SourceTrustError as exc:
        logger.error("review decision refused (fail closed): %s", exc)
        return 2

    logger.info(
        "Recorded %s for %s (content_hash=%s, revision %d, decided_by=%s)",
        decision.approval_status,
        decision.source_id,
        decision.content_hash[:16],
        decision.revision,
        decision.decided_by,
    )
    if decision.approval_status == APPROVAL_APPROVED:
        logger.info(
            "This approval covers these bytes only. Re-run full-rebuild or incremental-build so the "
            "stored provenance is refreshed; a sealed epoch cannot be changed in place."
        )
    return 0


def _handle_rewrite_feedback() -> int:
    from common.config import get_config_dict

    get_config_dict()
    from rewrite.feedback import RewriteFeedback

    RewriteFeedback().run_feedback_cycle()
    logger.info("Rewrite feedback cycle completed")
    return 0


def _handle_export_regression_candidates(args) -> int:
    """Build regression candidates and export the human-approved dataset.

    Collecting candidates is safe and repeatable. The export is where the gate
    matters: candidates start as PENDING_REVIEW, only a reviewer's explicit
    acceptance puts a case in the dataset, and a case missing a human-authored
    expectation fails the whole export rather than shipping an empty field.
    """
    from common.config import get_config_dict
    from offline.regression_candidates import RegressionCandidateError, RegressionCandidateLoop

    feedback_config = get_config_dict().get("offline", {}).get("feedback", {})
    store_path = args.store_path or feedback_config.get("store_path", "./data/feedback/feedback.sqlite3")
    output_dir = args.output_dir or feedback_config.get("output_dir", "./data/feedback")
    status = None if args.status == "all" else args.status

    loop = RegressionCandidateLoop(store_path, output_dir=output_dir)
    try:
        result = loop.run_export_cycle(status=status)
    except RegressionCandidateError as exc:
        logger.error("Regression candidate export failed closed: %s", exc)
        return 2
    finally:
        loop.close()

    logger.info(
        "Regression candidates: %d created (%d total; %d pending review, %d accepted, %d rejected)",
        result["candidates_created"],
        result["candidates_total"],
        result["pending_review"],
        result["accepted"],
        result["rejected"],
    )
    logger.info("Review queue: %s", result["review_queue"])
    logger.info("Regression dataset: %s", result["regression_dataset"])
    return 0


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.mode == "rewrite-feedback":
        return _handle_rewrite_feedback()

    handlers = {
        "create-index": _handle_create_index,
        "ingest": _handle_ingest,
        "ingest-text": _handle_ingest_text,
        "incremental-build": _handle_incremental_build,
        "full-rebuild": _handle_full_rebuild,
        "seal-epoch": _handle_seal_epoch,
        "review-source": _handle_review_source,
        "export-regression-candidates": _handle_export_regression_candidates,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
