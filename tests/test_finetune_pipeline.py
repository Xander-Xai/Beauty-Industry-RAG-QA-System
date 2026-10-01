"""Tests for offline/finetune_qlora.py — all mocked, no GPU required.

Note: datasets and peft are not installed in CI, so we inject proper
mock modules into sys.modules before importing finetune_qlora.
"""

import importlib
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock

import pytest

# ── Inject mock modules with proper __spec__ ──────────────────────
# peft module needs __spec__ for transformers.is_peft_available()
_peft_spec = importlib.machinery.ModuleSpec("peft", None, is_package=True)
_peft_mod = importlib.util.module_from_spec(_peft_spec)


class _MockLoraConfig:
    """Mock LoraConfig that accepts any kwargs."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __repr__(self):
        return f"MockLoraConfig({self.__dict__})"


_peft_mod.LoraConfig = _MockLoraConfig
_peft_mod.PeftMixedModel = MagicMock()
_peft_mod.PeftModel = MagicMock()
_peft_mod.get_peft_model = MagicMock()
_peft_mod.prepare_model_for_kbit_training = MagicMock()
_peft_mod.PEFT_TYPE_MAPPING = {}  # needed by transformers import
_peft_mod.get_peft_config = MagicMock()
sys.modules["peft"] = _peft_mod

# We must NOT have peft as a namespace that blocks lookups —
# reinstall the module so transformers sees it as a real package
importlib.invalidate_caches()

# trl module (needed for PEFT_AVAILABLE check)
_trl_spec = importlib.machinery.ModuleSpec("trl", None, is_package=True)
_trl_mod = importlib.util.module_from_spec(_trl_spec)
_trl_mod.SFTTrainer = MagicMock()
sys.modules["trl"] = _trl_mod

# datasets module
_ds_spec = importlib.machinery.ModuleSpec("datasets", None, is_package=True)
_ds_mod = importlib.util.module_from_spec(_ds_spec)
_ds_mod.Dataset = MagicMock()
_ds_mod.Dataset.from_list = MagicMock(side_effect=lambda items: items)
sys.modules["datasets"] = _ds_mod

from offline.finetune_qlora import (  # noqa: E402
    DATASETS_AVAILABLE,
    PEFT_AVAILABLE,
    _write_model_card,
    create_lora_config,
    load_training_data,
)


class TestModuleImports:
    """Verify module imports work with mocked datasets/peft."""

    def test_mocks_are_loaded(self):
        assert DATASETS_AVAILABLE is True
        assert PEFT_AVAILABLE is True


class TestLoadTrainingData:
    """Test training data loading."""

    def test_load_valid_data(self):
        data = [
            {"instruction": "回答", "input": "问句", "output": "答案"},
            {"instruction": "回答", "input": "问句2", "output": "答案2"},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            result = load_training_data(path, mock_tokenizer())
            assert len(result) == 2
        finally:
            os.unlink(path)

    def test_missing_keys_raises(self):
        data = [{"input": "只有input", "output": "只有output"}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            with pytest.raises(KeyError):
                load_training_data(path, mock_tokenizer())
        finally:
            os.unlink(path)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([], f)
            path = f.name
        try:
            result = load_training_data(path, mock_tokenizer())
            assert len(result) == 0
        finally:
            os.unlink(path)

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_training_data("/tmp/nonexistent_finetune_data.json", mock_tokenizer())


class TestCreateLoraConfig:
    """Test LoRA config creation."""

    def test_creates_config(self):
        args = mock_lora_args()
        config = create_lora_config(args)
        assert config.r == 16
        assert config.lora_alpha == 32
        assert "q_proj" in config.target_modules
        assert len(config.target_modules) == 7

    def test_custom_modules(self):
        args = mock_lora_args(target_modules="q_proj,v_proj")
        config = create_lora_config(args)
        assert len(config.target_modules) == 2

    def test_custom_rank(self):
        args = mock_lora_args(rank=8)
        config = create_lora_config(args)
        assert config.r == 8


class TestWriteModelCard:
    """Test model card generation."""

    def test_writes_readme(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = mock_data_args(output_dir=tmpdir)
            _write_model_card(args, mock_lora_args(), mock_model_args(), dataset_size=40)

            card_path = os.path.join(tmpdir, "README.md")
            assert os.path.exists(card_path)
            with open(card_path) as f:
                content = f.read()
            assert "QLoRA" in content
            assert "LoRA Configuration" in content
            assert "40 examples" in content
            assert "Deployment" in content
            assert "vLLM" in content


# ── Helpers ────────────────────────────────────────────────────────


class MockArgs:
    pass


def mock_tokenizer():
    class MockTokenizer:
        eos_token = "<eos>"
        padding_side = "right"
        pad_token = "<pad>"

        def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False):
            if not tokenize:
                return " ".join(m["content"] for m in messages if m.get("content"))
            return [1, 2, 3]

    return MockTokenizer()


def mock_model_args(quantization_bits=4, quant_type="nf4", use_double_quant=True):
    args = MockArgs()
    args.model_path = "./models/Qwen3-14B-Instruct"
    args.quantization_bits = quantization_bits
    args.quant_type = quant_type
    args.use_double_quant = use_double_quant
    return args


def mock_lora_args(
    rank=16, alpha=32, dropout=0.05, target_modules="q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj"
):
    args = MockArgs()
    args.lora_rank = rank
    args.lora_alpha = alpha
    args.lora_dropout = dropout
    args.lora_target_modules = target_modules
    return args


def mock_data_args(output_dir="./output", data_path="./data.json"):
    args = MockArgs()
    args.data_path = data_path
    args.output_dir = output_dir
    args.num_epochs = 3
    args.batch_size = 1
    args.gradient_accumulation_steps = 4
    args.learning_rate = 2e-4
    args.max_seq_length = 2048
    args.logging_steps = 10
    args.save_steps = 0
    args.save_total_limit = 1
    args.warmup_steps = 100
    args.fp16 = True
    args.validation_split = 0.0
    return args
