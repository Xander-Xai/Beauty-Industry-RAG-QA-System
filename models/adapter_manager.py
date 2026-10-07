"""
PEFT Adapter 生命周期管理器

支持 LoRA adapter 的发现、验证、加载、热切换和卸载。
生产模式下使用 PEFT PeftModel.from_pretrained，非生产模式下优雅降级。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdapterInfo:
    """Adapter 元数据"""

    name: str
    path: str
    base_model: str
    peft_version: str = ""
    rank: int = 0
    alpha: int = 0
    target_modules: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationResult:
    """Adapter 验证结果"""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class AdapterManager:
    """PEFT Adapter 生命周期管理器

    职责:
    - 扫描适配器目录以发现可用 LoRA adapter
    - 验证 adapter 与 base model 的兼容性
    - 加载 adapter（生产模式使用 PEFT，非生产模式优雅降级）
    - 运行时热切换 adapter 并在失败时自动回滚
    - 卸载 adapter 恢复到 base model

    用法::

        mgr = AdapterManager(
            base_model_name="Qwen/Qwen3-14B",
            adapter_dir="./adapters",
            default_adapter="regulation-lora",
        )
        adapters = mgr.discover()
        mgr.load("regulation-lora")
        mgr.switch("ecommerce-lora")
        mgr.unload()
    """

    # 标准权重文件名（按优先级排列）
    _WEIGHT_FILES = ("adapter_model.safetensors", "adapter_model.bin")

    def __init__(
        self,
        base_model_name: str,
        adapter_dir: str | None = None,
        default_adapter: str | None = None,
    ):
        if not base_model_name:
            raise ValueError("base_model_name is required")

        self._base_model_name = base_model_name
        self._adapter_dir = adapter_dir
        self._default_adapter = default_adapter
        self._adapters: dict[str, AdapterInfo] = {}
        self._current_adapter_name: str | None = None
        self._peft_model: Any = None  # PeftModel instance (production only)
        self._discovered = False
        logger.info(
            "AdapterManager 初始化: base_model=%s, adapter_dir=%s, default=%s",
            base_model_name,
            adapter_dir or "(none)",
            default_adapter or "(none)",
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_adapter(self) -> str | None:
        """当前加载的 adapter 名称"""
        return self._current_adapter_name

    @property
    def available_adapters(self) -> list[str]:
        """可用 adapter 名称列表（触发发现扫描）"""
        if not self._discovered:
            self.discover()
        return sorted(self._adapters.keys())

    # ------------------------------------------------------------------
    # 发现 (Discovery)
    # ------------------------------------------------------------------

    def discover(self) -> list[AdapterInfo]:
        """扫描 adapter_dir 并解析所有 adapter 元数据

        对 adapter_dir 下每个直接子目录查找 ``adapter_config.json``，
        解析出 :class:`AdapterInfo`。不包含 ``adapter_config.json``
        的目录会被跳过。

        Returns:
            list[AdapterInfo]: 发现的 adapter 列表
        """
        self._adapters.clear()
        self._discovered = True

        if not self._adapter_dir:
            return []

        adapter_path = Path(self._adapter_dir)
        if not adapter_path.is_dir():
            return []

        for entry in sorted(adapter_path.iterdir()):
            if not entry.is_dir():
                continue
            config_file = entry / "adapter_config.json"
            if not config_file.is_file():
                logger.debug("跳过 %s: 无 adapter_config.json", entry.name)
                continue

            info = self._parse_adapter_config(entry.name, str(entry), config_file)
            if info is not None:
                self._adapters[info.name] = info

        logger.info("发现 %d 个 adapter", len(self._adapters))
        return list(self._adapters.values())

    def _parse_adapter_config(self, name: str, path: str, config_file: Path) -> AdapterInfo | None:
        """解析单个 adapter_config.json 为 AdapterInfo"""
        try:
            with open(config_file, encoding="utf-8") as fh:
                cfg: dict[str, Any] = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("无法解析 %s: %s", config_file, exc)
            return None

        base_model = cfg.get("base_model_name_or_path", "")
        peft_version = cfg.get("peft_version", "")
        lora_config = cfg.get("lora", {}) or {}
        rank = cfg.get("r") or lora_config.get("r") or cfg.get("lora_r") or 0
        alpha = cfg.get("lora_alpha") or lora_config.get("lora_alpha") or 0
        target_modules: list[str] = cfg.get("target_modules") or lora_config.get("target_modules") or []

        return AdapterInfo(
            name=name,
            path=path,
            base_model=base_model,
            peft_version=peft_version,
            rank=int(rank) if rank else 0,
            alpha=int(alpha) if alpha else 0,
            target_modules=list(target_modules) if target_modules else [],
        )

    # ------------------------------------------------------------------
    # 验证 (Validation)
    # ------------------------------------------------------------------

    def validate(self, adapter_path: str) -> ValidationResult:
        """验证 adapter 路径上的配置和文件完整性

        检查:
        1. 路径存在
        2. ``adapter_config.json`` 存在且可解析
        3. ``base_model_name_or_path`` 与当前 base model 匹配（不匹配时输出警告）
        4. 权重文件存在（``adapter_model.safetensors`` 或 ``adapter_model.bin``）

        Args:
            adapter_path: adapter 目录的路径

        Returns:
            ValidationResult
        """
        errors: list[str] = []
        warnings: list[str] = []

        # 1. 路径存在
        path_obj = Path(adapter_path)
        if not path_obj.exists():
            return ValidationResult(
                is_valid=False,
                errors=[f"路径不存在: {adapter_path}"],
            )

        if not path_obj.is_dir():
            return ValidationResult(
                is_valid=False,
                errors=[f"路径不是目录: {adapter_path}"],
            )

        # 2. adapter_config.json 存在
        config_file = path_obj / "adapter_config.json"
        if not config_file.is_file():
            return ValidationResult(
                is_valid=False,
                errors=[f"缺少 adapter_config.json: {config_file}"],
            )

        # 解析 config
        try:
            with open(config_file, encoding="utf-8") as fh:
                cfg: dict[str, Any] = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            return ValidationResult(
                is_valid=False,
                errors=[f"adapter_config.json 解析失败: {exc}"],
            )

        # 3. Base model 匹配检查
        configured_base = cfg.get("base_model_name_or_path", "")
        if configured_base and configured_base != self._base_model_name:
            warnings.append(
                f"Base model 不匹配: adapter 配置为 '{configured_base}', 当前 manager 配置为 '{self._base_model_name}'"
            )

        # 4. 权重文件存在
        found_weight = False
        for wf in self._WEIGHT_FILES:
            if (path_obj / wf).is_file():
                found_weight = True
                break
        if not found_weight:
            errors.append(f"未找到权重文件 (试过: {', '.join(self._WEIGHT_FILES)})")

        is_valid = len(errors) == 0
        return ValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # 加载 (Load)
    # ------------------------------------------------------------------

    def load(self, adapter_name: str) -> bool | None:
        """加载指定名称的 adapter

        生产模式 (``is_production_mode() == True``):
            使用 PEFT ``PeftModel.from_pretrained`` 加载 adapter。
            若 PEFT 未安装或加载失败，记录日志并优雅降级。

        非生产模式:
            记录日志但不实际加载，返回 ``None`` 表示优雅降级。

        Args:
            adapter_name: 要加载的 adapter 名称（必须存在于可用 adapter 中）

        Returns:
            bool | None: 生产模式下返回 ``True``/``False``，非生产模式返回 ``None``
        """
        from common.config import is_production_mode

        # 确保 adapter 已发现
        if adapter_name not in self._adapters:
            if not self._discovered:
                self.discover()
            if adapter_name not in self._adapters:
                logger.error("未知 adapter: %s (可用: %s)", adapter_name, list(self._adapters))
                return False

        info = self._adapters[adapter_name]

        # 非生产模式: 优雅降级（返回 None）
        if not is_production_mode():
            logger.info("非生产模式，跳过 adapter 加载: %s (path=%s)", adapter_name, info.path)
            self._current_adapter_name = adapter_name
            return None

        # 生产模式: 使用 PEFT
        try:
            import torch
            from peft import PeftModel
            from transformers import AutoModelForCausalLM
        except ImportError as exc:
            logger.warning("PEFT 或 transformers 未安装，无法加载 adapter: %s", exc)
            return False

        try:
            base = self._get_base_model()
            if base is None:
                base = AutoModelForCausalLM.from_pretrained(
                    self._base_model_name,
                    torch_dtype=torch.float16,
                    device_map="auto",
                )
            self._peft_model = PeftModel.from_pretrained(base, info.path)
            self._current_adapter_name = adapter_name
            logger.info("Adapter 加载成功: %s", adapter_name)
            return True
        except Exception as exc:
            logger.error("Adapter 加载失败 (%s): %s", adapter_name, exc)
            self._peft_model = None
            return False

    def _get_base_model(self):
        """返回当前持有的 base model（如果有）"""
        if self._peft_model is not None:
            try:
                return self._peft_model.base_model
            except AttributeError:
                pass
        return None

    def _get_peft_model(self) -> Any | None:
        """返回当前持有的 PeftModel 实例（内部调试用）。"""
        return self._peft_model

    # ------------------------------------------------------------------
    # 热切换 (Hot-swap)
    # ------------------------------------------------------------------

    def switch(self, adapter_name: str) -> bool:
        """运行时热切换 adapter

        保存当前 adapter 名称，尝试加载目标 adapter。
        加载失败时自动回滚到之前的 adapter。

        Args:
            adapter_name: 目标 adapter 名称

        Returns:
            bool: 切换成功返回 ``True``，否则返回 ``False``
        """
        previous = self._current_adapter_name
        result = self.load(adapter_name)
        # 仅在显式返回 False 时视为失败
        # None（非生产模式降级）和 True 均视为成功
        if result is False:
            logger.warning("Adapter 切换失败 (%s)，回滚到 %s", adapter_name, previous)
            if previous is not None:
                self.load(previous)
            else:
                self.unload()
            return False
        return True

    # ------------------------------------------------------------------
    # 卸载 (Unload)
    # ------------------------------------------------------------------

    def unload(self) -> None:
        """卸载当前 adapter，恢复到 base model

        清除 PEFT model 引用，将 ``current_adapter`` 重置为 ``None``。
        如果没有加载任何 adapter，此操作为空操作。
        """
        if self._peft_model is not None:
            logger.info("卸载 adapter: %s", self._current_adapter_name)
            self._peft_model = None
        self._current_adapter_name = None
        logger.debug("Adapter 已卸载")
