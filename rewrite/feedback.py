"""
Query Rewrite 反馈闭环模块（PRD §16）

职责：
1. 追踪 intent / business_type 分类准确率
2. 周采样 500 条 rewrite 结果用于人工标注
3. 基于标注结果优化 Rewrite Prompt 模板
4. A/B 测试驱动的 Prompt 版本管理
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import random
import time
from datetime import datetime
from typing import Any, Optional

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)

# ── Rewrite 反馈配置 ──────────────────────────────────────────────────
REWRITE_FEEDBACK_CONFIG = {
    "sample_size": 500,                # 每周采样数
    "min_samples_for_update": 200,     # 触发 Prompt 更新的最少标注数
    "accuracy_threshold": 0.85,        # 准确率低于此值触发 Prompt 优化
    "output_dir": "./data/feedback/rewrite",
}

# ── Prompt 模板版本管理 ────────────────────────────────────────────────
PROMPT_VERSIONS_DIR = "./data/feedback/rewrite/prompt_versions"


class RewriteFeedback:
    """
    Query Rewrite 反馈闭环

    流程：
    1. 收集 Rewrite 结果日志
    2. 采样 + 导出标注任务
    3. 导入标注结果，计算准确率
    4. 准确率低于阈值时，触发 Prompt 优化
    """

    def __init__(self, output_dir: str = None):
        self.output_dir = output_dir or REWRITE_FEEDBACK_CONFIG["output_dir"]
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(PROMPT_VERSIONS_DIR, exist_ok=True)
        logger.info(f"RewriteFeedback 初始化: output_dir={self.output_dir}")

    # ─── 日志收集 ────────────────────────────────────────────────

    def collect_rewrite_logs(self, days: int = 7) -> list[dict]:
        """
        收集最近 N 天的 Rewrite 结果日志。

        日志格式:
        {
            "ts": float,
            "request_id": str,
            "query": str (hashed),
            "rewritten_query": str,
            "business_type": str,
            "intent": str,
            "confidence": float,
            "fallback": bool,
        }
        """
        logs = []

        # 从文件系统收集
        log_dir = os.path.join(self.output_dir, "logs")
        if not os.path.exists(log_dir):
            return logs

        cutoff = time.time() - (days * 86400)
        for fname in sorted(os.listdir(log_dir)):
            if not fname.startswith("rewrite_") or not fname.endswith(".jsonl"):
                continue
            fpath = os.path.join(log_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            log = json.loads(line)
                            if log.get("ts", 0) >= cutoff:
                                logs.append(log)
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.warning(f"读取日志文件失败 {fpath}: {e}")

        logger.info(f"Rewrite 日志收集: {len(logs)} 条")
        return logs

    def log_rewrite_result(
        self,
        request_id: str,
        query: str,
        rewritten_query: str,
        business_type: str,
        intent: str,
        confidence: float = 0.5,
        fallback: bool = False,
    ):
        """
        记录单条 Rewrite 结果（供后续反馈闭环使用）。

        在 pipeline 的 rewrite 阶段后调用。
        """
        log_dir = os.path.join(self.output_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)

        log_entry = {
            "ts": time.time(),
            "request_id": request_id,
            "query_hash": hashlib.sha256(query.encode()).hexdigest()[:16],
            "rewritten_query": rewritten_query,
            "business_type": business_type,
            "intent": intent,
            "confidence": confidence,
            "fallback": fallback,
            "annotation_intent": "",
            "annotation_business_type": "",
            "user_feedback": -1,
        }

        # 按日期分文件
        date_str = datetime.now().strftime("%Y%m%d")
        log_path = os.path.join(log_dir, f"rewrite_{date_str}.jsonl")

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    # ─── 采样与标注 ──────────────────────────────────────────────

    def sample_for_annotation(self, logs: list[dict], n: int = None) -> list[dict]:
        """分层采样 N 条 Rewrite 结果用于人工标注"""
        n = n or REWRITE_FEEDBACK_CONFIG["sample_size"]

        unannotated = [l for l in logs if l.get("user_feedback", -1) == -1]
        pool = unannotated if unannotated else logs

        if len(pool) <= n:
            return pool

        # 按 business_type 分层
        by_type: dict[str, list] = {}
        for log in pool:
            bt = log.get("business_type", "general")
            by_type.setdefault(bt, []).append(log)

        samples = []
        for bt, bt_logs in by_type.items():
            type_n = max(1, int(n * len(bt_logs) / len(pool)))
            samples.extend(random.sample(bt_logs, min(type_n, len(bt_logs))))

        remaining = [l for l in pool if l not in samples]
        if len(samples) < n and remaining:
            samples.extend(random.sample(remaining, min(n - len(samples), len(remaining))))

        return samples[:n]

    def export_annotation_task(self, samples: list[dict]) -> str:
        """导出 Rewrite 标注任务"""
        task_id = f"rewrite_annotation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        task_path = os.path.join(self.output_dir, f"{task_id}.jsonl")

        with open(task_path, "w", encoding="utf-8") as f:
            for sample in samples:
                export = {
                    "request_id": sample.get("request_id", ""),
                    "query_hash": sample.get("query_hash", ""),
                    "rewritten_query": sample.get("rewritten_query", ""),
                    "business_type": sample.get("business_type", ""),
                    "intent": sample.get("intent", ""),
                    "annotation_business_type": "",
                    "annotation_intent": "",
                    "user_feedback": -1,
                }
                f.write(json.dumps(export, ensure_ascii=False) + "\n")

        logger.info(f"Rewrite 标注任务导出: {task_path} ({len(samples)} 条)")
        return task_path

    def import_annotations(self, task_path: str) -> dict:
        """
        导入标注结果，计算准确率。

        Returns:
            {
                "imported": int,
                "intent_accuracy": float,
                "business_type_accuracy": float,
                "overall_accuracy": float,
            }
        """
        annotations = []
        with open(task_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("user_feedback", -1) != -1:
                        annotations.append(data)
                except json.JSONDecodeError:
                    continue

        if not annotations:
            return {"imported": 0, "intent_accuracy": 0.0, "business_type_accuracy": 0.0, "overall_accuracy": 0.0}

        # 计算 intent 准确率
        intent_correct = sum(
            1 for a in annotations
            if a.get("annotation_intent") == a.get("intent")
        )
        intent_accuracy = intent_correct / len(annotations)

        # 计算 business_type 准确率
        bt_correct = sum(
            1 for a in annotations
            if a.get("annotation_business_type") == a.get("business_type")
        )
        bt_accuracy = bt_correct / len(annotations)

        overall = (intent_accuracy + bt_accuracy) / 2

        # 保存标注结果
        output_path = os.path.join(
            self.output_dir,
            f"rewrite_annotated_{datetime.now().strftime('%Y%m%d')}.jsonl",
        )
        with open(output_path, "w", encoding="utf-8") as f:
            for a in annotations:
                f.write(json.dumps(a, ensure_ascii=False) + "\n")

        logger.info(
            f"Rewrite 标注导入: intent={intent_accuracy:.2%}, "
            f"business_type={bt_accuracy:.2%}, overall={overall:.2%}"
        )
        return {
            "imported": len(annotations),
            "intent_accuracy": intent_accuracy,
            "business_type_accuracy": bt_accuracy,
            "overall_accuracy": overall,
        }

    # ─── Prompt 优化 ────────────────────────────────────────────

    def should_update_prompt(self, accuracy: float) -> bool:
        """判断是否需要更新 Prompt 模板"""
        return accuracy < REWRITE_FEEDBACK_CONFIG["accuracy_threshold"]

    def save_prompt_version(self, prompt_text: str, version_tag: str = None) -> str:
        """
        保存 Prompt 模板版本（A/B 测试用）。

        Returns:
            版本文件路径
        """
        version_tag = version_tag or f"v{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        version_path = os.path.join(PROMPT_VERSIONS_DIR, f"rewrite_prompt_{version_tag}.json")

        version_data = {
            "version": version_tag,
            "created_at": datetime.now().isoformat(),
            "prompt_text": prompt_text,
            "accuracy": None,  # 待 A/B 测试后回填
            "status": "candidate",
        }

        with open(version_path, "w", encoding="utf-8") as f:
            json.dump(version_data, f, ensure_ascii=False, indent=2)

        logger.info(f"Prompt 版本保存: {version_path}")
        return version_path

    def get_active_prompt_version(self) -> Optional[dict]:
        """获取当前活跃的 Prompt 版本"""
        for fname in sorted(os.listdir(PROMPT_VERSIONS_DIR), reverse=True):
            if not fname.startswith("rewrite_prompt_") or not fname.endswith(".json"):
                continue
            fpath = os.path.join(PROMPT_VERSIONS_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("status") == "active":
                    return data
            except Exception:
                continue
        return None

    # ─── 完整反馈周期 ──────────────────────────────────────────

    def run_feedback_cycle(self) -> dict:
        """
        执行 Rewrite 反馈闭环周期。

        Returns:
            {
                "collected_logs": int,
                "sampled": int,
                "annotation_task": str,
                "intent_accuracy": float,
                "bt_accuracy": float,
                "should_update_prompt": bool,
            }
        """
        logs = self.collect_rewrite_logs(days=7)
        samples = self.sample_for_annotation(logs)
        task_path = self.export_annotation_task(samples)

        # 检查已有标注结果
        annotated_files = [
            f for f in os.listdir(self.output_dir)
            if f.startswith("rewrite_annotated_") and f.endswith(".jsonl")
        ]

        accuracy_result = {"intent_accuracy": 0.0, "bt_accuracy": 0.0}
        for af in annotated_files:
            result = self.import_annotations(os.path.join(self.output_dir, af))
            if result["imported"] > 0:
                accuracy_result = result
                break

        should_update = self.should_update_prompt(accuracy_result.get("overall_accuracy", 0.0))

        result = {
            "collected_logs": len(logs),
            "sampled": len(samples),
            "annotation_task": task_path,
            "intent_accuracy": accuracy_result.get("intent_accuracy", 0.0),
            "bt_accuracy": accuracy_result.get("business_type_accuracy", 0.0),
            "should_update_prompt": should_update,
        }

        logger.info(f"Rewrite 反馈周期完成: {result}")
        return result
