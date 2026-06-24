"""
AdapterManager 单元测试

覆盖 adapter 生命周期管理器的所有核心路径:
- 初始化 (无目录、不存在目录)
- 发现扫描 (空目录、有效目录、跳过无关目录)
- 验证 (路径缺失、配置缺失、基座模型不匹配、有效适配器)
- 加载 (非生产模式优雅降级)
- 卸载 (空操作、重复卸载安全)
- 热切换 (不存在的 adapter、回滚)

所有测试使用临时目录创建模拟 adapter 目录结构。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# torch mock shim — 避免在未安装 torch 的环境下 import 失败
try:
    import torch  # noqa: F401
except ImportError:
    _fake_torch = types.ModuleType("torch")
    _fake_cuda = types.ModuleType("torch.cuda")
    _fake_cuda.is_available = lambda: False
    _fake_torch.cuda = _fake_cuda
    sys.modules["torch"] = _fake_torch
    sys.modules["torch.cuda"] = _fake_cuda


# ── Fixtures ──


@pytest.fixture
def base_model() -> str:
    return "Qwen/Qwen2.5-14B"


@pytest.fixture
def adapter_config() -> dict:
    return {
        "base_model_name_or_path": "Qwen/Qwen2.5-14B",
        "peft_version": "0.14.0",
        "r": 16,
        "lora_alpha": 32,
        "target_modules": ["q_proj", "v_proj"],
    }


@pytest.fixture
def adapter_config_other_base() -> dict:
    return {
        "base_model_name_or_path": "Other/Model",
        "peft_version": "0.14.0",
        "r": 8,
        "lora_alpha": 16,
        "target_modules": ["q_proj"],
    }


@pytest.fixture
def make_adapter_dir():
    """创建包含 adapter 配置和权重文件的临时目录。"""

    def _make(config: dict, weight_file: str = "adapter_model.safetensors") -> str:
        tmp = tempfile.TemporaryDirectory()
        cfg_path = Path(tmp.name) / "adapter_config.json"
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(config, fh)
        # 创建占位权重文件
        weight_path = Path(tmp.name) / weight_file
        weight_path.write_text("pretend weights")
        return tmp.name

    return _make


# ── 初始化测试 ──


class TestAdapterManagerInit:
    """测试 AdapterManager 初始化"""

    def test_init_without_adapter_dir(self, base_model):
        """无 adapter_dir 不应报错，available_adapters 为空"""
        from models.adapter_manager import AdapterManager

        mgr = AdapterManager(base_model_name=base_model)
        assert mgr.current_adapter is None
        assert mgr.available_adapters == []

    def test_init_with_nonexistent_dir(self, base_model):
        """adapter_dir 不存在不应报错，available_adapters 为空"""
        from models.adapter_manager import AdapterManager

        mgr = AdapterManager(
            base_model_name=base_model,
            adapter_dir="/tmp/nonexistent_adapter_dir_12345",
        )
        assert mgr.current_adapter is None
        assert mgr.available_adapters == []

    def test_init_with_default_adapter(self, base_model):
        """使用 default_adapter 应记录名称，但不加载"""
        from models.adapter_manager import AdapterManager

        mgr = AdapterManager(
            base_model_name=base_model,
            default_adapter="regulation-lora",
        )
        # 仅记录默认 adapter 名称，不触发加载
        assert mgr.current_adapter is None
        # default_adapter 未实际创建，不在可用列表中
        assert "regulation-lora" not in mgr.available_adapters

    def test_init_requires_base_model(self):
        """base_model_name 为空时应抛出 ValueError"""
        from models.adapter_manager import AdapterManager

        with pytest.raises(ValueError, match="base_model_name is required"):
            AdapterManager(base_model_name="")


# ── 发现测试 ──


class TestAdapterManagerDiscover:
    """测试 adapter 发现扫描"""

    def test_discover_empty_dir(self, base_model):
        """空目录应返回空列表"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = AdapterManager(
                base_model_name=base_model,
                adapter_dir=tmp,
            )
            result = mgr.discover()
            assert result == []
            assert mgr.available_adapters == []

    def test_discover_finds_adapter(self, base_model, adapter_config):
        """发现包含 config 的目录应返回 AdapterInfo 并填充正确字段"""
        from models.adapter_manager import AdapterManager, AdapterInfo

        with tempfile.TemporaryDirectory() as tmp:
            adapter_path = Path(tmp) / "regulation-lora"
            adapter_path.mkdir()
            cfg_file = adapter_path / "adapter_config.json"
            with open(cfg_file, "w", encoding="utf-8") as fh:
                json.dump(adapter_config, fh)
            (adapter_path / "adapter_model.safetensors").write_text("weights")

            mgr = AdapterManager(
                base_model_name=base_model,
                adapter_dir=tmp,
            )
            result = mgr.discover()

            assert len(result) == 1
            info = result[0]
            assert isinstance(info, AdapterInfo)
            assert info.name == "regulation-lora"
            assert info.base_model == "Qwen/Qwen2.5-14B"
            assert info.peft_version == "0.14.0"
            assert info.rank == 16
            assert info.alpha == 32
            assert info.target_modules == ["q_proj", "v_proj"]
            assert mgr.available_adapters == ["regulation-lora"]

    def test_discover_skips_dirs_without_config(self, base_model):
        """不含 adapter_config.json 的目录应被跳过"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            # 创建有 config 的目录
            valid = Path(tmp) / "valid-lora"
            valid.mkdir()
            cfg_file = valid / "adapter_config.json"
            with open(cfg_file, "w", encoding="utf-8") as fh:
                json.dump(
                    {"base_model_name_or_path": base_model, "r": 8},
                    fh,
                )
            (valid / "adapter_model.safetensors").write_text("weights")

            # 创建无 config 的目录
            no_config = Path(tmp) / "no-config-dir"
            no_config.mkdir()
            (no_config / "random_file.txt").write_text("hello")

            # 创建普通文件（应跳过）
            (Path(tmp) / "file.txt").write_text("not a dir")

            mgr = AdapterManager(
                base_model_name=base_model,
                adapter_dir=tmp,
            )
            result = mgr.discover()

            assert len(result) == 1
            assert result[0].name == "valid-lora"
            assert mgr.available_adapters == ["valid-lora"]

    def test_discover_multiple_adapters(self, base_model, adapter_config):
        """多个有效 adapter 目录都应被发现"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            names = []
            for name in ["lora-a", "lora-b", "lora-c"]:
                d = Path(tmp) / name
                d.mkdir()
                cfg = dict(adapter_config)
                cfg["r"] = {"lora-a": 8, "lora-b": 16, "lora-c": 32}[name]
                with open(d / "adapter_config.json", "w", encoding="utf-8") as fh:
                    json.dump(cfg, fh)
                (d / "adapter_model.safetensors").write_text("weights")
                names.append(name)

            mgr = AdapterManager(
                base_model_name=base_model,
                adapter_dir=tmp,
            )
            mgr.discover()

            assert sorted(mgr.available_adapters) == sorted(names)
            assert mgr.available_adapters == ["lora-a", "lora-b", "lora-c"]  # sorted

    def test_discover_parses_lora_nested_config(self, base_model):
        """解析嵌套 lora 配置字段"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            adapter_path = Path(tmp) / "nested-lora"
            adapter_path.mkdir()
            cfg = {
                "base_model_name_or_path": base_model,
                "lora": {
                    "r": 16,
                    "lora_alpha": 32,
                    "target_modules": ["q_proj", "k_proj", "v_proj"],
                },
            }
            with open(adapter_path / "adapter_config.json", "w", encoding="utf-8") as fh:
                json.dump(cfg, fh)
            (adapter_path / "adapter_model.safetensors").write_text("weights")

            mgr = AdapterManager(
                base_model_name=base_model,
                adapter_dir=tmp,
            )
            result = mgr.discover()
            assert len(result) == 1
            assert result[0].rank == 16
            assert result[0].alpha == 32
            assert result[0].target_modules == ["q_proj", "k_proj", "v_proj"]


# ── 验证测试 ──


class TestAdapterManagerValidate:
    """测试 adapter 验证"""

    def test_validate_missing_path(self, base_model):
        """路径不存在应返回 is_valid=False 和错误信息"""
        from models.adapter_manager import AdapterManager

        mgr = AdapterManager(base_model_name=base_model)
        result = mgr.validate("/tmp/definitely_not_exists_xyz_999")
        assert result.is_valid is False
        assert len(result.errors) > 0
        assert any("不存在" in e for e in result.errors)

    def test_validate_missing_config(self, base_model):
        """缺少 adapter_config.json 应返回 is_valid=False"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            mgr = AdapterManager(base_model_name=base_model)
            result = mgr.validate(tmp)
            assert result.is_valid is False
            assert any("adapter_config.json" in e for e in result.errors)

    def test_validate_base_model_mismatch(self, base_model):
        """base model 不匹配应返回 is_valid=True 但包含警告"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            cfg_file = Path(tmp) / "adapter_config.json"
            with open(cfg_file, "w", encoding="utf-8") as fh:
                json.dump(
                    {"base_model_name_or_path": "Different/Model", "r": 8},
                    fh,
                )
            (Path(tmp) / "adapter_model.safetensors").write_text("weights")

            mgr = AdapterManager(base_model_name=base_model)
            result = mgr.validate(tmp)
            assert result.is_valid is True
            assert len(result.warnings) > 0
            assert any("不匹配" in w for w in result.warnings)

    def test_validate_valid_adapter(self, base_model):
        """有效 adapter 应返回 is_valid=True，无错误无警告"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            cfg_file = Path(tmp) / "adapter_config.json"
            with open(cfg_file, "w", encoding="utf-8") as fh:
                json.dump(
                    {"base_model_name_or_path": base_model, "r": 8},
                    fh,
                )
            (Path(tmp) / "adapter_model.safetensors").write_text("weights")

            mgr = AdapterManager(base_model_name=base_model)
            result = mgr.validate(tmp)
            assert result.is_valid is True
            assert result.errors == []
            assert result.warnings == []

    def test_validate_missing_weights(self, base_model):
        """缺少权重文件应返回 is_valid=False"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            cfg_file = Path(tmp) / "adapter_config.json"
            with open(cfg_file, "w", encoding="utf-8") as fh:
                json.dump(
                    {"base_model_name_or_path": base_model, "r": 8},
                    fh,
                )
            # 不创建权重文件

            mgr = AdapterManager(base_model_name=base_model)
            result = mgr.validate(tmp)
            assert result.is_valid is False
            assert any("权重文件" in e for e in result.errors)

    def test_validate_adapter_model_bin_fallback(self, base_model):
        """应接受 adapter_model.bin 作为有效权重文件"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            cfg_file = Path(tmp) / "adapter_config.json"
            with open(cfg_file, "w", encoding="utf-8") as fh:
                json.dump(
                    {"base_model_name_or_path": base_model, "r": 8},
                    fh,
                )
            # 使用 .bin 而非 .safetensors
            (Path(tmp) / "adapter_model.bin").write_text("bin weights")

            mgr = AdapterManager(base_model_name=base_model)
            result = mgr.validate(tmp)
            assert result.is_valid is True
            assert result.errors == []


# ── 加载测试 ──


class TestAdapterManagerLoad:
    """测试 adapter 加载"""

    def test_load_non_production(self, base_model, adapter_config):
        """非生产模式下加载应返回 None（优雅降级）"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            adapter_path = Path(tmp) / "regulation-lora"
            adapter_path.mkdir()
            with open(adapter_path / "adapter_config.json", "w", encoding="utf-8") as fh:
                json.dump(adapter_config, fh)
            (adapter_path / "adapter_model.safetensors").write_text("weights")

            mgr = AdapterManager(
                base_model_name=base_model,
                adapter_dir=tmp,
            )
            # discover 使 adapter 可见
            mgr.discover()
            result = mgr.load("regulation-lora")
            # 非生产模式下返回 None（优雅降级），且记录 current_adapter
            assert result is None
            assert mgr.current_adapter == "regulation-lora"

    def test_load_nonexistent_adapter(self, base_model):
        """加载不存在的 adapter 应返回 False"""
        from models.adapter_manager import AdapterManager

        mgr = AdapterManager(base_model_name=base_model)
        result = mgr.load("nonexistent_adapter")
        assert result is False
        assert mgr.current_adapter is None

    def test_load_without_discover_scans_first(self, base_model, adapter_config):
        """load 前未调用 discover 应自动扫描"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            adapter_path = Path(tmp) / "auto-lora"
            adapter_path.mkdir()
            with open(adapter_path / "adapter_config.json", "w", encoding="utf-8") as fh:
                json.dump(adapter_config, fh)
            (adapter_path / "adapter_model.safetensors").write_text("weights")

            mgr = AdapterManager(
                base_model_name=base_model,
                adapter_dir=tmp,
            )
            # 不手动 discover，load 应自动发现
            result = mgr.load("auto-lora")
            assert result is None  # 非生产模式
            assert mgr.current_adapter == "auto-lora"


# ── 热切换测试 ──


class TestAdapterManagerSwitch:
    """测试 adapter 热切换"""

    def test_switch_to_nonexistent_adapter(self, base_model):
        """切换到不存在的 adapter 不应报错，current_adapter 保持不变"""
        from models.adapter_manager import AdapterManager

        mgr = AdapterManager(base_model_name=base_model)
        # 初始为 None
        assert mgr.current_adapter is None

        # 切换到一个不存在的 adapter
        result = mgr.switch("nonexistent")
        assert result is False
        assert mgr.current_adapter is None  # 回滚到 None

    def test_switch_between_adapters(self, base_model, adapter_config):
        """在两个有效 adapter 间切换"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            # 创建两个 adapter
            for name in ["lora-a", "lora-b"]:
                d = Path(tmp) / name
                d.mkdir()
                cfg = dict(adapter_config)
                cfg["r"] = 8
                with open(d / "adapter_config.json", "w", encoding="utf-8") as fh:
                    json.dump(cfg, fh)
                (d / "adapter_model.safetensors").write_text("weights")

            mgr = AdapterManager(
                base_model_name=base_model,
                adapter_dir=tmp,
            )

            # 先加载第一个
            mgr.discover()
            mgr.load("lora-a")
            assert mgr.current_adapter == "lora-a"

            # 切换到第二个
            result = mgr.switch("lora-b")
            assert result is True
            assert mgr.current_adapter == "lora-b"


# ── 卸载测试 ──


class TestAdapterManagerUnload:
    """测试 adapter 卸载"""

    def test_unload_when_nothing_loaded(self, base_model):
        """未加载任何 adapter 时卸载不应报错"""
        from models.adapter_manager import AdapterManager

        mgr = AdapterManager(base_model_name=base_model)
        assert mgr.current_adapter is None
        # 应静默成功
        mgr.unload()
        assert mgr.current_adapter is None

    def test_double_unload_is_safe(self, base_model):
        """重复卸载应安全"""
        from models.adapter_manager import AdapterManager

        mgr = AdapterManager(base_model_name=base_model)
        mgr.unload()
        mgr.unload()  # 第二次不应报错
        assert mgr.current_adapter is None

    def test_unload_clears_adapter(self, base_model, adapter_config):
        """卸载后 current_adapter 应为 None"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "test-lora"
            d.mkdir()
            with open(d / "adapter_config.json", "w", encoding="utf-8") as fh:
                json.dump(adapter_config, fh)
            (d / "adapter_model.safetensors").write_text("weights")

            mgr = AdapterManager(
                base_model_name=base_model,
                adapter_dir=tmp,
            )
            mgr.discover()
            mgr.load("test-lora")
            assert mgr.current_adapter == "test-lora"

            mgr.unload()
            assert mgr.current_adapter is None


# ── 集成测试 ──


class TestAdapterManagerIntegration:
    """综合场景测试"""

    def test_full_lifecycle(self, base_model):
        """完整的生命周期: 发现 -> 加载 -> 切换 -> 卸载"""
        from models.adapter_manager import AdapterManager

        with tempfile.TemporaryDirectory() as tmp:
            # 创建两个 adapter
            for name in ["lora-v1", "lora-v2"]:
                d = Path(tmp) / name
                d.mkdir()
                cfg = {
                    "base_model_name_or_path": base_model,
                    "peft_version": "0.14.0",
                    "r": 16,
                    "lora_alpha": 32,
                    "target_modules": ["q_proj", "v_proj"],
                }
                with open(d / "adapter_config.json", "w", encoding="utf-8") as fh:
                    json.dump(cfg, fh)
                (d / "adapter_model.safetensors").write_text("weights")

            mgr = AdapterManager(
                base_model_name=base_model,
                adapter_dir=tmp,
            )

            # 发现
            assert mgr.available_adapters == ["lora-v1", "lora-v2"]

            # 验证
            result = mgr.validate(str(Path(tmp) / "lora-v1"))
            assert result.is_valid is True

            # 加载 (非生产模式)
            mgr.load("lora-v1")
            assert mgr.current_adapter == "lora-v1"

            # 切换
            mgr.switch("lora-v2")
            assert mgr.current_adapter == "lora-v2"

            # 卸载
            mgr.unload()
            assert mgr.current_adapter is None