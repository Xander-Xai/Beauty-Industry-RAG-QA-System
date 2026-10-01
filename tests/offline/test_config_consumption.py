"""Prove the new config keys are actually consumed at runtime."""

from __future__ import annotations

from common.config import get_config_dict
from offline.scheduler import load_scheduler_config
from offline.snapshot_builder import configured_document_processor


def test_new_knowledge_base_and_offline_keys_are_consumed():
    config = get_config_dict()
    knowledge_base = config["knowledge_base"]
    for key in (
        "supported_extensions",
        "state_db_path",
        "image_batch_size",
        "ocr_batch_size",
        "max_binary_document_bytes",
        "ocr_min_text_chars",
        "pdf_render_dpi",
        "xlsx_rows_per_block",
    ):
        assert key in knowledge_base, f"missing knowledge_base.{key}"

    assert config["embedding"]["image_clip"]["model_revision"]
    assert "scheduler" in config["offline"]

    processor = configured_document_processor(config)
    assert processor.xlsx_rows_per_block == knowledge_base["xlsx_rows_per_block"]
    assert processor.ocr_min_text_chars == knowledge_base["ocr_min_text_chars"]
    assert processor.pdf_render_dpi == knowledge_base["pdf_render_dpi"]
    assert processor.max_binary_document_bytes == knowledge_base["max_binary_document_bytes"]

    scheduler = load_scheduler_config(config)
    assert scheduler.incremental_cron == config["offline"]["scheduler"]["incremental_cron"]
    assert scheduler.full_rebuild_cron == config["offline"]["scheduler"]["full_rebuild_cron"]
    assert scheduler.auto_seal == config["offline"]["scheduler"]["auto_seal"]


def test_image_clip_model_revision_flows_into_embedding_version():
    from offline.embeddings import CLIPImageEmbedder

    config = get_config_dict()
    image_config = config["embedding"]["image_clip"]
    embedder = CLIPImageEmbedder(
        image_config["model_path"],
        int(image_config.get("dimension", 512)),
        model_revision=image_config.get("model_revision"),
    )
    assert embedder.embedding_version.startswith(image_config["model_revision"])
