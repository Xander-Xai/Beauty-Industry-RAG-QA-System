"""
黄金数据集验证脚本

校验 JSONL 黄金数据集的格式完整性和内容质量。

这里有一个必须区分开的事实，否则会产生误导性结论：

* **格式校验（本脚本的默认职责）** 只回答「字段是否齐全、类型是否正确」。
  它**不是**「相关性标签已验证」，也**不是**「检索指标可归因」。
* **可归因性（attributability）** 由 `benchmarks.golden_set_contract` 的 v2 契约判定：
  只有每一条 ground-truth 段落都能解析到真实索引中的 `(doc_id, chunk_id)`、
  并带有 `corpus_version` 与人工评审状态时，才允许把检索指标当作证据。

当前提交的 `golden_set.jsonl` 版本**格式合规但不可归因（0/301）**：它没有
`doc_id` / `chunk_id` / `source_id`，relevance 只能退化为规范化精确文本匹配。
本脚本因此在格式通过后**显式披露**可归因计数，避免「全部合规」被读成
「相关性标签已验证」。阻塞与数据需求见 `docs/benchmark-data-quality.md`、issue #86。

Usage:
    python -m tests.evaluation.validate_golden_set --dataset tests/evaluation/golden_set.jsonl

    # 需要把「不可归因」也当作失败（例如真实 benchmark 前置门禁）时：
    python -m tests.evaluation.validate_golden_set \
        --dataset tests/evaluation/golden_set.jsonl --require-attributable
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

#: 退出码：格式失败
EXIT_FORMAT_INVALID = 1
#: 退出码：格式通过但不可归因（仅当 --require-attributable 时使用）
EXIT_NOT_ATTRIBUTABLE = 2


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


def attributability_report(path: str) -> dict:
    """按 v2 契约测量数据集的可归因性。

    只报告「有多少行可以作为检索证据」以及缺失字段的机器可读原因，绝不补齐或推断
    任何缺失字段。返回键：``total`` / ``valid`` / ``invalid`` / ``invalid_reasons``
    / ``attributable``。
    """
    from benchmarks.golden_set_contract import load_contract_rows, validate_dataset

    rows = load_contract_rows(path)
    report = validate_dataset(rows)
    return {
        "total": report.total,
        "valid": report.valid_count,
        "invalid": report.invalid_count,
        "invalid_reasons": report.invalid_reasons(),
        "attributable": report.valid_count > 0,
    }


def _render_attributability(report: dict) -> list[str]:
    """把格式合规与「相关性标签已验证」明确区分开。"""
    lines = [
        "",
        "── 可归因性（relevance attributability, golden-set-contract/v2）──",
        f"可归因行数: {report['valid']} / {report['total']}",
    ]
    if report["attributable"]:
        lines.append("该数据集可作为可归因的检索评测输入。")
    else:
        reasons = ", ".join(f"{name}×{count}" for name, count in sorted(report["invalid_reasons"].items()))
        lines += [
            "该数据集**不可归因**：检索指标若在此运行，只能退化为规范化精确文本匹配，",
            "无法区分「检索失败」与「文本对齐失败」。",
            f"缺失字段（机器可读）: {reasons or '-'}",
            "",
            "注意：**格式校验通过 ≠ golden set 的相关性标签已验证**。",
            "身份字段（doc_id/chunk_id/source_id）、visual_required、complexity_label 与",
            "人工评审状态仍然缺失；真实语料、封存 epoch 与人工标注是外部依赖。",
            "阻塞与数据需求见 docs/benchmark-data-quality.md 与 issue #86；",
            "真实运行见 issue #18。",
        ]
    return lines


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。返回进程退出码。"""
    parser = argparse.ArgumentParser(description="黄金数据集验证工具")
    parser.add_argument(
        "--dataset",
        type=str,
        required=True,
        help="JSONL 黄金数据集路径",
    )
    parser.add_argument(
        "--require-attributable",
        action="store_true",
        help="格式通过但数据集不可归因时，以非零退出码失败（默认为仅披露）",
    )
    args = parser.parse_args(argv)

    errors = validate_golden_set(args.dataset)

    if errors:
        print(f"❌ 格式校验失败 — 发现 {len(errors)} 个问题:\n")
        for err in errors:
            print(f"  • {err}")
        return EXIT_FORMAT_INVALID

    dataset = load_golden_set(args.dataset)
    print(f"✅ 格式校验通过 — {len(dataset)} 条数据字段齐全")

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

    report = attributability_report(args.dataset)
    print("\n".join(_render_attributability(report)))

    if args.require_attributable and not report["attributable"]:
        print(
            f"\n❌ --require-attributable: 可归因行数为 0/{report['total']}，拒绝把该数据集当作检索证据。",
            file=sys.stderr,
        )
        return EXIT_NOT_ATTRIBUTABLE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
