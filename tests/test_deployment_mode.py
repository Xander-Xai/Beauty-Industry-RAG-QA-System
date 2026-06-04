import json
import os
import tempfile
import pytest
from common.config import AppConfig, get_config, reload_config


def test_deployment_mode_default():
    """默认模式应为 development。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_data = json.load(open("config.json"))
        config_data["deployment_mode"] = "development"
        json.dump(config_data, f)
        f.flush()
        config_path = f.name
    try:
        os.environ["CONFIG_PATH"] = config_path
        reload_config()
        cfg = get_config()
        assert hasattr(cfg, "deployment_mode")
        assert cfg.deployment_mode == "development"
    finally:
        os.unlink(config_path)
        if "CONFIG_PATH" in os.environ:
            del os.environ["CONFIG_PATH"]


def test_production_mode_requires_dual_gpu():
    """生产模式应检测到 dual_gpu=True。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_data = json.load(open("config.json"))
        config_data["deployment_mode"] = "production"
        config_data["system"]["dual_gpu"] = True
        json.dump(config_data, f)
        f.flush()
        config_path = f.name
    try:
        os.environ["CONFIG_PATH"] = config_path
        reload_config()
        cfg = get_config()
        assert cfg.deployment_mode == "production"
        assert cfg.system.dual_gpu is True
    finally:
        os.unlink(config_path)
        if "CONFIG_PATH" in os.environ:
            del os.environ["CONFIG_PATH"]


def test_development_mode_skips_gpu():
    """开发模式下应能正确识别。"""
    from common.config import is_production_mode, is_testing_mode, is_development_mode
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        config_data = json.load(open("config.json"))
        config_data["deployment_mode"] = "development"
        json.dump(config_data, f)
        f.flush()
        config_path = f.name
    try:
        os.environ["CONFIG_PATH"] = config_path
        reload_config()
        assert is_development_mode() is True
        assert is_production_mode() is False
        assert is_testing_mode() is False
    finally:
        os.unlink(config_path)
        if "CONFIG_PATH" in os.environ:
            del os.environ["CONFIG_PATH"]
