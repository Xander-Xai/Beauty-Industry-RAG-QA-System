"""
A/B 实验平台基础框架（PRD §16）

职责：
1. 流量分割：按 user_id hash 将用户分配到实验组/对照组
2. 实验管理：创建、查询、终止实验
3. 指标采集：各组的延迟、命中率、准确率
4. 统计检验：基于样本量的显著性检验（z-test / chi-squared）
5. 灰度发布：1 周实验期后根据指标决定是否全量发布

实验场景：
- Rewrite Prompt 版本对比
- Evidence Gate 阈值对比
- RRF 权重对比
- CLIP 路由策略对比
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)

# ── 实验配置 ──────────────────────────────────────────────────────────
EXPERIMENT_DIR = "./data/experiments"
EXPERIMENT_CONFIG_FILE = os.path.join(EXPERIMENT_DIR, "experiments.json")


@dataclass
class ExperimentVariant:
    """实验变体"""
    name: str                           # "control" / "treatment_a" / ...
    traffic_ratio: float = 0.5          # 流量比例 (0.0 - 1.0)
    config_override: dict = field(default_factory=dict)  # 覆盖配置项
    description: str = ""


@dataclass
class Experiment:
    """A/B 实验"""
    experiment_id: str
    name: str
    description: str
    variants: list[ExperimentVariant]
    status: str = "draft"               # draft / running / completed / terminated
    created_at: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration_days: int = 7              # 实验持续天数
    min_sample_size: int = 200          # 每组最小样本量
    metrics: dict = field(default_factory=dict)  # 各组指标


class ABExperimentPlatform:
    """
    A/B 实验平台

    使用方法：
    1. 创建实验 → 2. 启动实验 → 3. 分流 → 4. 采集指标 → 5. 分析结果
    """

    def __init__(self, experiment_dir: str = None):
        self.experiment_dir = experiment_dir or EXPERIMENT_DIR
        os.makedirs(self.experiment_dir, exist_ok=True)
        self._experiments: dict[str, Experiment] = {}
        self._load_experiments()
        logger.info(f"ABExperimentPlatform 初始化: {len(self._experiments)} 个活跃实验")

    def _load_experiments(self):
        """从文件加载实验配置"""
        if os.path.exists(EXPERIMENT_CONFIG_FILE):
            try:
                with open(EXPERIMENT_CONFIG_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                for exp_id, exp_data in data.items():
                    variants = [
                        ExperimentVariant(**v) for v in exp_data.pop("variants", [])
                    ]
                    self._experiments[exp_id] = Experiment(
                        variants=variants, **exp_data
                    )
            except Exception as e:
                logger.warning(f"加载实验配置失败: {e}")

    def _save_experiments(self):
        """持久化实验配置"""
        data = {}
        for exp_id, exp in self._experiments.items():
            exp_dict = {
                "experiment_id": exp.experiment_id,
                "name": exp.name,
                "description": exp.description,
                "variants": [
                    {
                        "name": v.name,
                        "traffic_ratio": v.traffic_ratio,
                        "config_override": v.config_override,
                        "description": v.description,
                    }
                    for v in exp.variants
                ],
                "status": exp.status,
                "created_at": exp.created_at,
                "started_at": exp.started_at,
                "ended_at": exp.ended_at,
                "duration_days": exp.duration_days,
                "min_sample_size": exp.min_sample_size,
                "metrics": exp.metrics,
            }
            data[exp_id] = exp_dict

        with open(EXPERIMENT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ─── 实验管理 ────────────────────────────────────────────────

    def create_experiment(
        self,
        name: str,
        variants: list[dict],
        description: str = "",
        duration_days: int = 7,
        min_sample_size: int = 200,
    ) -> str:
        """
        创建新实验。

        Args:
            name: 实验名称
            variants: 变体列表 [{"name": "control", "traffic_ratio": 0.5, "config_override": {...}}, ...]
            description: 实验描述
            duration_days: 实验持续天数
            min_sample_size: 每组最小样本量

        Returns:
            experiment_id
        """
        exp_id = f"exp_{hashlib.md5(f'{name}_{time.time()}'.encode()).hexdigest()[:8]}"

        parsed_variants = [
            ExperimentVariant(
                name=v["name"],
                traffic_ratio=v.get("traffic_ratio", 0.5 / len(variants)),
                config_override=v.get("config_override", {}),
                description=v.get("description", ""),
            )
            for v in variants
        ]

        experiment = Experiment(
            experiment_id=exp_id,
            name=name,
            description=description,
            variants=parsed_variants,
            status="draft",
            created_at=datetime.now().isoformat(),
            duration_days=duration_days,
            min_sample_size=min_sample_size,
        )

        self._experiments[exp_id] = experiment
        self._save_experiments()

        logger.info(f"实验创建: {exp_id} ({name}), {len(parsed_variants)} 个变体")
        return exp_id

    def start_experiment(self, experiment_id: str) -> bool:
        """启动实验"""
        exp = self._experiments.get(experiment_id)
        if not exp or exp.status != "draft":
            return False

        exp.status = "running"
        exp.started_at = datetime.now().isoformat()
        self._save_experiments()

        logger.info(f"实验启动: {experiment_id} ({exp.name})")
        return True

    def terminate_experiment(self, experiment_id: str) -> bool:
        """终止实验"""
        exp = self._experiments.get(experiment_id)
        if not exp or exp.status != "running":
            return False

        exp.status = "terminated"
        exp.ended_at = datetime.now().isoformat()
        self._save_experiments()

        logger.info(f"实验终止: {experiment_id}")
        return True

    def get_running_experiments(self) -> list[Experiment]:
        """获取所有运行中的实验"""
        return [e for e in self._experiments.values() if e.status == "running"]

    # ─── 流量分流 ────────────────────────────────────────────────

    def assign_variant(self, user_id: str, experiment_id: str) -> ExperimentVariant | None:
        """
        根据 user_id 确定性地分配实验变体。

        使用 user_id hash 保证同一用户始终进入同一组。
        """
        exp = self._experiments.get(experiment_id)
        if not exp or exp.status != "running":
            return None

        # 确定性 hash 分流
        hash_val = int(hashlib.md5(f"{user_id}:{experiment_id}".encode()).hexdigest(), 16)
        bucket = (hash_val % 10000) / 10000.0  # 0.0 - 1.0

        cumulative = 0.0
        for variant in exp.variants:
            cumulative += variant.traffic_ratio
            if bucket < cumulative:
                return variant

        # fallback 到最后一个变体
        return exp.variants[-1] if exp.variants else None

    def get_variant_config(self, user_id: str, experiment_id: str) -> dict:
        """
        获取用户对应的实验配置覆盖。

        合并实验变体的 config_override 到基础配置。
        """
        variant = self.assign_variant(user_id, experiment_id)
        if not variant:
            return {}

        return variant.config_override

    # ─── 指标采集 ────────────────────────────────────────────────

    def record_metric(
        self,
        experiment_id: str,
        user_id: str,
        metric_name: str,
        value: float,
    ):
        """
        记录实验指标。

        Args:
            experiment_id: 实验 ID
            user_id: 用户 ID
            metric_name: 指标名称（latency_ms / cache_hit / evidence_score / user_feedback）
            value: 指标值
        """
        exp = self._experiments.get(experiment_id)
        if not exp or exp.status != "running":
            return

        variant = self.assign_variant(user_id, experiment_id)
        if not variant:
            return

        # 按变体分组存储指标
        variant_metrics = exp.metrics.setdefault(variant.name, {})
        metric_list = variant_metrics.setdefault(metric_name, [])
        metric_list.append({
            "user_id": user_id,
            "value": value,
            "ts": time.time(),
        })

        # 每 100 条自动持久化
        if len(metric_list) % 100 == 0:
            self._save_experiments()

    # ─── 统计分析 ────────────────────────────────────────────────

    def analyze_experiment(self, experiment_id: str) -> dict:
        """
        分析实验结果。

        对每个指标进行组间比较，计算均值、标准差、显著性。

        Returns:
            {
                "experiment_id": str,
                "status": str,
                "variants": {
                    "control": {"latency_ms": {"mean": ..., "std": ..., "n": ...}, ...},
                    "treatment": {...},
                },
                "significant_results": [{"metric": str, "winner": str, "p_value": float}],
            }
        """
        exp = self._experiments.get(experiment_id)
        if not exp:
            return {"error": "experiment not found"}

        results = {
            "experiment_id": experiment_id,
            "name": exp.name,
            "status": exp.status,
            "variants": {},
            "significant_results": [],
        }

        for variant in exp.variants:
            v_metrics = exp.metrics.get(variant.name, {})
            variant_stats = {}
            for metric_name, values in v_metrics.items():
                nums = [v["value"] for v in values]
                if not nums:
                    continue
                mean = sum(nums) / len(nums)
                std = (sum((x - mean) ** 2 for x in nums) / len(nums)) ** 0.5
                variant_stats[metric_name] = {
                    "mean": round(mean, 4),
                    "std": round(std, 4),
                    "n": len(nums),
                }
            results["variants"][variant.name] = variant_stats

        # 组间显著性检验（简化 z-test）
        if len(exp.variants) >= 2:
            v0_name = exp.variants[0].name
            v1_name = exp.variants[1].name
            v0_metrics = exp.metrics.get(v0_name, {})
            v1_metrics = exp.metrics.get(v1_name, {})

            for metric_name in v0_metrics:
                if metric_name not in v1_metrics:
                    continue
                v0_values = [v["value"] for v in v0_metrics[metric_name]]
                v1_values = [v["value"] for v in v1_metrics[metric_name]]

                if len(v0_values) < 30 or len(v1_values) < 30:
                    continue

                z_stat, p_value = self._z_test(v0_values, v1_values)
                if p_value < 0.05:
                    winner = v0_name if sum(v0_values) / len(v0_values) < sum(v1_values) / len(v1_values) else v1_name
                    results["significant_results"].append({
                        "metric": metric_name,
                        "winner": winner,
                        "z_stat": round(z_stat, 4),
                        "p_value": round(p_value, 6),
                    })

        return results

    @staticmethod
    def _z_test(sample_a: list[float], sample_b: list[float]) -> tuple[float, float]:
        """
        简化双样本 z-test。

        Returns:
            (z_statistic, p_value)
        """
        import math

        n_a, n_b = len(sample_a), len(sample_b)
        mean_a = sum(sample_a) / n_a
        mean_b = sum(sample_b) / n_b
        var_a = sum((x - mean_a) ** 2 for x in sample_a) / n_a
        var_b = sum((x - mean_b) ** 2 for x in sample_b) / n_b

        se = math.sqrt(var_a / n_a + var_b / n_b)
        if se == 0:
            return 0.0, 1.0

        z = (mean_a - mean_b) / se

        # 近似 p-value（双尾）
        p = 2 * (1 - ABExperimentPlatform._normal_cdf(abs(z)))
        return z, p

    @staticmethod
    def _normal_cdf(x: float) -> float:
        """标准正态分布 CDF 近似（Abramowitz & Stegun）"""
        import math
        a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
        p = 0.3275911
        sign = 1 if x >= 0 else -1
        x = abs(x) / math.sqrt(2)
        t = 1.0 / (1.0 + p * x)
        y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
        return 0.5 * (1.0 + sign * y)

    # ─── 实验生命周期 ────────────────────────────────────────────

    def check_experiment_expiry(self) -> list[str]:
        """
        检查是否有实验到期，自动标记为 completed。

        Returns:
            已完成的实验 ID 列表
        """
        completed = []
        now = datetime.now()

        for exp_id, exp in self._experiments.items():
            if exp.status != "running" or not exp.started_at:
                continue

            started = datetime.fromisoformat(exp.started_at)
            if now - started > timedelta(days=exp.duration_days):
                exp.status = "completed"
                exp.ended_at = now.isoformat()
                completed.append(exp_id)
                logger.info(f"实验自动完成: {exp_id} ({exp.name})")

        if completed:
            self._save_experiments()

        return completed


# ── 全局单例 ──────────────────────────────────────────────────────────
_platform_instance: ABExperimentPlatform | None = None


def get_ab_platform() -> ABExperimentPlatform:
    """获取 A/B 实验平台单例"""
    global _platform_instance
    if _platform_instance is None:
        _platform_instance = ABExperimentPlatform()
    return _platform_instance
