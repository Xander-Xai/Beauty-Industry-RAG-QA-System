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

    document_type = classify_source(args.source)
    source_id = args.source_id
    if not source_id:
        from common.config import get_config_dict

        data_dir = Path(get_config_dict().get("knowledge_base", {}).get("data_dir", "./data")).resolve()
        try:
            source_id = Path(args.source).resolve().relative_to(data_dir).as_posix()
        except ValueError:
            logger.error("source is outside knowledge_base.data_dir; provide --source-id")
            return 2
    source = IngestionSource(
        source_id=source_id,
        path=args.source,
        document_type=document_type,
        role_mask=args.role_mask,
        dept_mask=args.dept_mask,
    )
    builder = configured_snapshot_builder()
    chunks, images = builder.ingest_source(source, args.epoch)
    logger.info("Ingested %d chunks and %d images into epoch %s", chunks, images, args.epoch)
    return 0


def _handle_ingest_text(args) -> int:
    from offline.text_ingestion import configured_text_ingestion_service

    chunks = configured_text_ingestion_service().ingest(
        args.source,
        role_mask=args.role_mask,
        dept_mask=args.dept_mask,
        doc_version_epoch=args.epoch,
        source_id=args.source_id,
    )
    logger.info("Ingested %d text chunks", len(chunks))
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
    recorded distinctly because it bypasses the snapshot check.
    """
    from offline.snapshot_builder import configured_snapshot_builder

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


def _handle_rewrite_feedback() -> int:
    from common.config import get_config_dict

    get_config_dict()
    from rewrite.feedback import RewriteFeedback

    RewriteFeedback().run_feedback_cycle()
    logger.info("Rewrite feedback cycle completed")
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
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
