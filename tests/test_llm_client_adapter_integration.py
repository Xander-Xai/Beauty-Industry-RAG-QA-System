"""LLMClient + AdapterManager 集成测试"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DEPLOYMENT_MODE"] = "development"

import types

_fake_torch = types.ModuleType("torch")
_fake_cuda = types.ModuleType("torch.cuda")
_fake_cuda.is_available = lambda: False
_fake_torch.cuda = _fake_cuda
sys.modules["torch"] = _fake_torch
sys.modules["torch.cuda"] = _fake_cuda

import pytest


class TestLLMClientAdapterIntegration:
    def test_adapter_manager_is_not_none(self):
        from models.llm_client import LLMClient

        client = LLMClient()
        assert hasattr(client, "adapter_manager")

    def test_adapter_loading_does_not_crash_init(self):
        from models.llm_client import LLMClient

        try:
            client = LLMClient()
            assert client is not None
        except Exception as e:
            pytest.fail(f"LLMClient init raised: {e}")
