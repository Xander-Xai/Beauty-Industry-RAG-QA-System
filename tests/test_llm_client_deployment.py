import pytest
from unittest.mock import patch, MagicMock
from models.llm_client import LLMClient


def test_single_card_mode_reuses_endpoint():
    """单卡模式下 14B 请求应降级到 4B endpoint。"""
    mock_json_config = {
        "generation": {"max_conversation_rounds": 6, "prompt_version": "v2.1"}
    }

    with patch("models.llm_client.config", mock_json_config), \
         patch("models.llm_client.StatelessRouter", create=True), \
         patch("common.config.is_production_mode", return_value=False):
        client = LLMClient()
        endpoint = client._resolve_endpoint("qwen3-14b")
        assert endpoint == "gen_4b"


def test_production_mode_preserves_14b():
    """生产模式下 14B 请求应路由到 14B endpoint。"""
    mock_json_config = {
        "generation": {"max_conversation_rounds": 6, "prompt_version": "v2.1"}
    }

    with patch("models.llm_client.config", mock_json_config), \
         patch("models.llm_client.StatelessRouter", create=True), \
         patch("common.config.is_production_mode", return_value=True):
        client = LLMClient()
        endpoint = client._resolve_endpoint("qwen3-14b")
        assert endpoint == "gen_14b"


def test_4b_always_stays_4b():
    """4B 请求在任何模式下都路由到 4B。"""
    mock_json_config = {
        "generation": {"max_conversation_rounds": 6, "prompt_version": "v2.1"}
    }

    with patch("models.llm_client.config", mock_json_config), \
         patch("models.llm_client.StatelessRouter", create=True), \
         patch("common.config.is_production_mode", return_value=False):
        client = LLMClient()
        endpoint = client._resolve_endpoint("qwen3-4b")
        assert endpoint == "gen_4b"
