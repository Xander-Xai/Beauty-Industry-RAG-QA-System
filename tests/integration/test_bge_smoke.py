"""Real configured-BGE smoke test.

Skipped by default. Run explicitly with local model assets present:

    RUN_MODEL_SMOKE=1 python3 -m pytest tests/integration/test_bge_smoke.py -v
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.model_smoke
def test_configured_bge_model_smoke():
    if os.environ.get("RUN_MODEL_SMOKE", "").lower() not in {"1", "true", "yes"}:
        pytest.skip("set RUN_MODEL_SMOKE=1 to run the real BGE smoke")

    from common.config import get_config_dict
    from scripts.smoke_bge_ingestion import run_smoke

    config = get_config_dict()
    text_config = config["embedding"]["text"]
    knowledge_base = config.get("knowledge_base", {})
    code = run_smoke(
        text_config["model_path"],
        int(text_config["dimension"]),
        int(knowledge_base.get("embedding_batch_size", 32)),
        text_config.get("model_revision"),
    )
    if code == 3:
        pytest.skip("EXTERNAL_MODEL_ASSET_REQUIRED: configured BGE model assets are not present")
    assert code == 0
