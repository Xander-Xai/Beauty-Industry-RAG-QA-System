"""
Query Rewrite 反馈闭环模块（PRD §16）

职责：
1. 追踪 intent / business_type 分类准确率
2. 周采样 500 条 rewrite 结果用于人工标注
3. 基于标注结果优化 Rewrite Prompt 模板
4. A/B 测试驱动的 Prompt 版本管理
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from datetime import datetime

from common.config import get_config_dict

config = get_config_dict()

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
                with open(fpath, encoding="utf-8") as f:
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
        with open(task_path, encoding="utf-8") as f:
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

    def generate_prompt_suggestion(self, annotations: list[dict]) -> dict:
        """
        PRD §12.2: 基于标注反馈自动生成 Prompt 修改建议。

        分析错误分类模式，生成针对性的 Prompt 增补指令。

        Returns:
            {
                "current_accuracy": float,
                "error_patterns": list[str],
                "suggested_additions": list[str],
                "new_prompt_draft": str,
            }
        """
        if not annotations:
            return {"current_accuracy": 0.0, "error_patterns": [], "suggested_additions": [], "new_prompt_draft": ""}

        # 分析错误模式
        intent_errors = {}
        bt_errors = {}
        for a in annotations:
            predicted_intent = a.get("intent", "")
            actual_intent = a.get("annotation_intent", "")
            if predicted_intent != actual_intent:
                key = f"{predicted_intent}→{actual_intent}"
                intent_errors[key] = intent_errors.get(key, 0) + 1

            predicted_bt = a.get("business_type", "")
            actual_bt = a.get("annotation_business_type", "")
            if predicted_bt != actual_bt:
                key = f"{predicted_bt}→{actual_bt}"
                bt_errors[key] = bt_errors.get(key, 0) + 1

        # 生成错误模式描述
        error_patterns = []
        for pattern, count in sorted(intent_errors.items(), key=lambda x: -x[1])[:5]:
            error_patterns.append(f"intent 误判: {pattern} ({count}次)")
        for pattern, count in sorted(bt_errors.items(), key=lambda x: -x[1])[:5]:
            error_patterns.append(f"business_type 误判: {pattern} ({count}次)")

        # 基于错误模式生成 Prompt 增补指令
        suggested_additions = []

        # 针对常见的 intent 误判生成修正指令
        if any("formulation→" in p for p in error_patterns):
            suggested_additions.append(
                "注意：formulation（配方）类查询通常涉及成分名称、配比、工艺流程，"
                "请仔细区分 formulation 与 development 的区别。"
            )
        if any("compliance→" in p or "→compliance" in p for p in error_patterns):
            suggested_additions.append(
                "注意：compliance（合规）类查询必须明确涉及法规条款、标准编号或合规要求，"
                "不能仅凭关键词判断。"
            )
        if any("ingredient→" in p for p in error_patterns):
            suggested_additions.append(
                "注意：ingredient（成分）类查询聚焦于单一成分的属性、功效或安全性，"
                "与 formulation（配方）的多成分配比查询不同。"
            )
        if any("general→regulation" in p for p in error_patterns):
            suggested_additions.append(
                "注意：只有明确涉及法规条款号、国家标准编号、合规审批流程时才归类为 regulation，"
                "一般性的产品问题应归类为 general。"
            )

        if not suggested_additions:
            suggested_additions.append(
                "请更准确地理解查询意图，参考上述错误模式进行分类。"
            )

        # 生成新 Prompt 草稿（增补版本）
        current_version = self.get_active_prompt_version()
        current_prompt = current_version.get("prompt_text", "") if current_version else ""

        new_prompt_draft = current_prompt + "\n\n## 分类修正指令（自动生成）\n"
        for i, addition in enumerate(suggested_additions, 1):
            new_prompt_draft += f"{i}. {addition}\n"

        # 计算当前准确率
        correct = sum(
            1 for a in annotations
            if a.get("annotation_intent") == a.get("intent")
            and a.get("annotation_business_type") == a.get("business_type")
        )
        current_accuracy = correct / len(annotations) if annotations else 0.0

        return {
            "current_accuracy": current_accuracy,
            "error_patterns": error_patterns,
            "suggested_additions": suggested_additions,
            "new_prompt_draft": new_prompt_draft,
        }

    def auto_update_prompt(self, annotations: list[dict]) -> dict:
        """
        PRD §12.2: 自动触发 Prompt 更新（仅生成草稿，不自动激活）。

        当标注准确率低于阈值时：
        1. 分析错误模式
        2. 生成新 Prompt 草稿
        3. 保存为 candidate 版本
        4. 等待人工审核后激活

        Returns:
            {
                "triggered": bool,
                "accuracy": float,
                "new_version_path": str or None,
                "error_patterns": list[str],
            }
        """
        suggestion = self.generate_prompt_suggestion(annotations)

        if not self.should_update_prompt(suggestion["current_accuracy"]):
            logger.info(
                f"准确率 {suggestion['current_accuracy']:.2%} ≥ 阈值，无需更新 Prompt"
            )
            return {
                "triggered": False,
                "accuracy": suggestion["current_accuracy"],
                "new_version_path": None,
                "error_patterns": [],
            }

        # 保存新 Prompt 草稿（candidate 状态，不自动激活）
        new_version_path = self.save_prompt_version(suggestion["new_prompt_draft"])

        logger.info(
            f"自动更新 Prompt: 准确率={suggestion['current_accuracy']:.2%} < "
            f"{REWRITE_FEEDBACK_CONFIG['accuracy_threshold']:.2%}, "
            f"新版本保存至 {new_version_path}"
        )

        return {
            "triggered": True,
            "accuracy": suggestion["current_accuracy"],
            "new_version_path": new_version_path,
            "error_patterns": suggestion["error_patterns"],
        }

    def save_prompt_version(self, prompt_text: str, version_tag: str = None) -> str:
        """
        保存 Prompt 模板版本（A/B 测试用）。

        PRD §10.2: prompt_version 由 Prompt Registry 管理，
        修改 Prompt 后自动递增。版本号格式: v{YYYYMMDD}_{序号}。

        Returns:
            版本文件路径
        """
        # PRD §10.2: 自动递增版本号
        if version_tag is None:
            existing = self._list_prompt_versions()
            today = datetime.now().strftime("%Y%m%d")
            today_versions = [v for v in existing if v.startswith(f"v{today}")]
            seq = len(today_versions) + 1
            version_tag = f"v{today}_{seq:03d}"

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

        logger.info(f"Prompt 版本保存: {version_path} (auto version: {version_tag})")
        return version_path

    def activate_prompt_version(self, version_tag: str) -> bool:
        """
        PRD §10.2: 激活指定 Prompt 版本，同时自动更新 config.json 中的
        prompt_version 字段，确保 Cache Key 自动变更、旧缓存自然失效。

        Returns:
            是否成功激活
        """
        target_path = os.path.join(PROMPT_VERSIONS_DIR, f"rewrite_prompt_{version_tag}.json")
        if not os.path.exists(target_path):
            logger.warning(f"Prompt 版本不存在: {version_tag}")
            return False

        # 将所有其他 active 版本降级为 completed
        for fname in os.listdir(PROMPT_VERSIONS_DIR):
            if not fname.startswith("rewrite_prompt_") or not fname.endswith(".json"):
                continue
            fpath = os.path.join(PROMPT_VERSIONS_DIR, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("status") == "active":
                    data["status"] = "completed"
                    with open(fpath, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                continue

        # 激活目标版本
        with open(target_path, encoding="utf-8") as f:
            data = json.load(f)
        data["status"] = "active"
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # PRD §10.2: 自动更新 config.json 中的 prompt_version
        self._update_config_prompt_version(version_tag)

        logger.info(f"Prompt 版本激活: {version_tag}")
        return True

    def _update_config_prompt_version(self, version_tag: str):
        """更新 config.json 中的 generation.prompt_version"""
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "config.json"
        )
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            cfg.setdefault("generation", {})["prompt_version"] = version_tag
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            logger.info(f"config.json prompt_version 更新为: {version_tag}")
        except Exception as e:
            logger.error(f"更新 config.json prompt_version 失败: {e}")

    def _list_prompt_versions(self) -> list[str]:
        """列出所有已保存的 Prompt 版本号"""
        versions = []
        if not os.path.exists(PROMPT_VERSIONS_DIR):
            return versions
        for fname in os.listdir(PROMPT_VERSIONS_DIR):
            if fname.startswith("rewrite_prompt_") and fname.endswith(".json"):
                tag = fname[len("rewrite_prompt_"):-len(".json")]
                versions.append(tag)
        return sorted(versions)

    def get_active_prompt_version(self) -> dict | None:
        """获取当前活跃的 Prompt 版本"""
        for fname in sorted(os.listdir(PROMPT_VERSIONS_DIR), reverse=True):
            if not fname.startswith("rewrite_prompt_") or not fname.endswith(".json"):
                continue
            fpath = os.path.join(PROMPT_VERSIONS_DIR, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
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

        PRD §12.2: 收集日志 → 采样 → 标注 → 准确率评估 → 自动 Prompt 更新建议

        Returns:
            {
                "collected_logs": int,
                "sampled": int,
                "annotation_task": str,
                "intent_accuracy": float,
                "bt_accuracy": float,
                "should_update_prompt": bool,
                "auto_update": dict,
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

        accuracy_result = {"intent_accuracy": 0.0, "business_type_accuracy": 0.0, "overall_accuracy": 0.0}
        all_annotations = []
        for af in annotated_files:
            result = self.import_annotations(os.path.join(self.output_dir, af))
            if result["imported"] > 0:
                accuracy_result = result
                # 收集所有标注数据用于 Prompt 分析
                try:
                    with open(os.path.join(self.output_dir, af), encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    data = json.loads(line)
                                    if data.get("user_feedback", -1) != -1:
                                        all_annotations.append(data)
                                except json.JSONDecodeError:
                                    continue
                except Exception:
                    pass
                break

        should_update = self.should_update_prompt(accuracy_result.get("overall_accuracy", 0.0))

        # PRD §12.2: 自动 Prompt 更新（生成草稿，不自动激活）
        auto_update_result = self.auto_update_prompt(all_annotations)

        result = {
            "collected_logs": len(logs),
            "sampled": len(samples),
            "annotation_task": task_path,
            "intent_accuracy": accuracy_result.get("intent_accuracy", 0.0),
            "bt_accuracy": accuracy_result.get("business_type_accuracy", 0.0),
            "should_update_prompt": should_update,
            "auto_update": auto_update_result,
        }

        logger.info(f"Rewrite 反馈周期完成: {result}")
        return result
