"""Offline knowledge base CLI: indexing, ingestion, rebuild, sealing, feedback."""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


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
    from offline.elasticsearch_writer import ElasticsearchWriter
    from offline.qdrant_writer import ensure_cosine_collection
    from offline.snapshot_builder import configured_snapshot_builder

    builder = configured_snapshot_builder()
    if args.recreate and not args.yes:
        logger.error("create-index --recreate requires --yes")
        return 2
    ensure_cosine_collection(
        builder.text_writer.client, builder.text_writer.collection_name, builder.text_writer.dimension
    )
    ensure_cosine_collection(
        builder.image_writer.client, builder.image_writer.collection_name, builder.image_writer.dimension
    )
    if builder.es_writer is not None and isinstance(builder.es_writer, ElasticsearchWriter):
        builder.es_writer.ensure_index(recreate=args.recreate, confirm=args.yes)
    logger.info("Indexes verified (text, image, elasticsearch).")
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
    from offline.text_ingestion import configured_text_ingestion_service

    service = configured_text_ingestion_service()
    if args.skip_validation:
        service.writer.seal_epoch(args.epoch)
        logger.warning("Sealed epoch %s without validation (--skip-validation).", args.epoch)
        return 0
    validate_and_seal = getattr(service, "validate_and_seal", None)
    if validate_and_seal is not None:
        validate_and_seal(args.epoch)
    else:
        service.writer.seal_epoch(args.epoch)
    logger.info(
        "Validated and sealed epoch %s; switch knowledge_version_epoch only after verification.",
        args.epoch,
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
