"""Offline text ingestion and supported feedback utility commands."""

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Offline knowledge base tools")
    parser.add_argument("command", nargs="?", choices=["ingest-text"])
    parser.add_argument("source", nargs="?", help="Path to a UTF-8 .txt source document")
    parser.add_argument("--role-mask", type=int)
    parser.add_argument("--dept-mask", type=int)
    parser.add_argument("--epoch")
    parser.add_argument("--mode", choices=["rewrite-feedback"], help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.mode == "rewrite-feedback":
        from common.config import get_config_dict

        get_config_dict()
        from rewrite.feedback import RewriteFeedback

        fb = RewriteFeedback()
        fb.run_feedback_cycle()
        logger.info("Rewrite feedback cycle completed")
        return 0

    if args.command == "ingest-text":
        if not args.source or args.role_mask is None or args.dept_mask is None or not args.epoch:
            parser.error("ingest-text requires SOURCE, --role-mask, --dept-mask, and --epoch")
        from offline.text_ingestion import configured_text_ingestion_service

        chunks = configured_text_ingestion_service().ingest(
            args.source,
            role_mask=args.role_mask,
            dept_mask=args.dept_mask,
            doc_version_epoch=args.epoch,
        )
        logger.info("Ingested %d text chunks", len(chunks))
        return 0

    print(
        "No offline ingestion command selected; only 'ingest-text' is supported.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
