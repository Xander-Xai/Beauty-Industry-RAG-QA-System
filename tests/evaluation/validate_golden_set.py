"""
黄金数据集验证脚本

校验 JSONL 黄金数据集的格式完整性和内容质量。

Usage:
    python -m tests.evaluation.validate_golden_set --dataset tests/evaluation/golden_set.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tests.evaluation.ragas_eval import load_golden_set

# 必需字段
REQUIRED_KEYS = {"question", "answer", "contexts", "ground_truth"}
# 可选分类字段
OPTIONAL_KEYS = {"business_type", "difficulty"}
# 允许的 business_type 值
VALID_BUSINESS_TYPES = {"ingredient", "regulation", "formula", "image", "general", "product"}
# 允许的 difficulty 值
VALID_DIFFICULTIES = {"easy", "medium", "hard"}
# 最小条目数
MIN_ENTRIES = 300


def validate_golden_set(path: str) -> list[str]:
    """验证黄金数据集，返回错误列表。空列表表示全部通过。"""
    errors: list[str] = []

    # 1. 文件存在性
    if not Path(path).is_file():
        errors.append(f"文件不存在: {path}")
        return errors

    # 2. 加载数据集
    try:
        dataset = load_golden_set(path)
    except Exception as exc:
        errors.append(f"加载数据集失败: {exc}")
        return errors

    # 3. 条目数量
    if len(dataset) == 0:
        errors.append("数据集为空，无任何条目")
        return errors

    if len(dataset) < MIN_ENTRIES:
        errors.append(f"条目数 ({len(dataset)}) 少于推荐最小值 ({MIN_ENTRIES})")

    # 4. 逐条校验
    for i, entry in enumerate(dataset):
        prefix = f"条目 #{i + 1}"

        # 必需字段
        missing = REQUIRED_KEYS - set(entry.keys())
        if missing:
            errors.append(f"{prefix}: 缺少必需字段 {missing}")
            continue

        # 空值检查
        for key in ("question", "answer", "ground_truth"):
            val = entry.get(key, "")
            if not val or (isinstance(val, str) and not val.strip()):
                errors.append(f"{prefix}: 字段 '{key}' 为空")

        # contexts 结构检查
        contexts = entry.get("contexts")
        if not isinstance(contexts, list):
            errors.append(f"{prefix}: 'contexts' 不是列表")
        elif len(contexts) == 0:
            errors.append(f"{prefix}: 'contexts' 为空列表")
        else:
            for j, ctx in enumerate(contexts):
                if not isinstance(ctx, str):
                    errors.append(f"{prefix}: contexts[{j}] 不是字符串")
                elif not ctx.strip():
                    errors.append(f"{prefix}: contexts[{j}] 为空字符串")

        # 可选分类字段
        business_type = entry.get("business_type")
        if business_type is not None and business_type not in VALID_BUSINESS_TYPES:
            errors.append(f"{prefix}: business_type '{business_type}' 不在允许集合 {VALID_BUSINESS_TYPES}")

        difficulty = entry.get("difficulty")
        if difficulty is not None and difficulty not in VALID_DIFFICULTIES:
            errors.append(f"{prefix}: difficulty '{difficulty}' 不在允许集合 {VALID_DIFFICULTIES}")

    # 5. 分类覆盖统计
    if len(dataset) >= MIN_ENTRIES:
        bt_counts: dict[str, int] = {}
        diff_counts: dict[str, int] = {}
        for entry in dataset:
            bt = entry.get("business_type", "unspecified")
            diff = entry.get("difficulty", "unspecified")
            bt_counts[bt] = bt_counts.get(bt, 0) + 1
            diff_counts[diff] = diff_counts.get(diff, 0) + 1

        # 检查是否所有主要类型都有覆盖
        for bt in VALID_BUSINESS_TYPES:
            if bt_counts.get(bt, 0) == 0:
                errors.append(f"缺少 business_type='{bt}' 的条目")

        for diff in VALID_DIFFICULTIES:
            if diff_counts.get(diff, 0) == 0:
                errors.append(f"缺少 difficulty='{diff}' 的条目")

    return errors


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="黄金数据集验证工具")
    parser.add_argument(
        "--dataset",
        type=str,
        required=True,
        help="JSONL 黄金数据集路径",
    )
    args = parser.parse_args()

    errors = validate_golden_set(args.dataset)

    if errors:
        print(f"❌ 验证失败 — 发现 {len(errors)} 个问题:\n")
        for err in errors:
            print(f"  • {err}")
        sys.exit(1)
    else:
        dataset = load_golden_set(args.dataset)
        print(f"✅ 验证通过 — {len(dataset)} 条数据全部合规")
        # 打印统计
        bt_counts: dict[str, int] = {}
        diff_counts: dict[str, int] = {}
        for entry in dataset:
            bt = entry.get("business_type", "unspecified")
            diff = entry.get("difficulty", "unspecified")
            bt_counts[bt] = bt_counts.get(bt, 0) + 1
            diff_counts[diff] = diff_counts.get(diff, 0) + 1
        print(f"\n  按业务类型: {dict(sorted(bt_counts.items()))}")
        print(f"  按难度: {dict(sorted(diff_counts.items()))}")


if __name__ == "__main__":
    main()
