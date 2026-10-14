# v2 标注集说明 — 合成候选，非标注结论

**本目录下的标注资产是人工构造的候选夹具，不是真实化妆品语料，也不是标注结论。**

## 合规声明

`annotation_workpackage_v1.jsonl` 的行是**候选**，不是标注结论。

本目录所有文件的法规名称、标准号、成分最大使用浓度、备案号格式与功效倍数均为
**虚构占位内容**，未经任何监管机构或第三方标准原文校对：

| 文件 | 行数 | 用途 |
|---|---:|---|
| `annotation_workpackage_v1.jsonl` | 70 | 待人工评审的候选标注工作表 |
| `annotations_v1.jsonl` | 0 | 空文件，标注工作尚未执行 |
| `annotations.schema.json` | — | JSON Schema 定义，不含业务数据 |

这些内容存在的唯一目的，是给人工标注流程与评测 harness 提供一条**形状真实**的输入。它们**不得**用于配方开发、合规判定、成分限用判断，
也不能作为安全评估依据。完整边界见仓库根目录的
[`DATA_PROVENANCE.md`](../../DATA_PROVENANCE.md) 第 4 节。

## 特别注意

标注工作表中部分行的 `question` 会故意提到并不存在的
"公司内部文件"，用来验证系统是否会编造引用。这类诱饵是**虚构的**，
不代表任何真实组织的文档。

`expected_behavior` 描述的是**评测意图**——当语料中确实存在
显式限值时，系统应当给出并引用它——**不是**对某个具体数值的正确性背书。
在语料被替换为经授权的公开法规原文之前，这些用例只能用于验证"是否会编造"，
不能用于验证"数值是否正确"。

## 替换为真实语料时

数据契约（字段、分发版本、标注来源、评审状态）由
`benchmarks/golden_set_contract.py` 与 `benchmarks/provenance.py` 定义，
替换语料时契约保持不变。来源许可无法确认的材料，先进入隔离区
（`offline/source_trust.py` 的 `UNTRUSTED` 信任级别），人工批准后才可激活。
准入流程见 [`docs/benchmark-data-quality.md`](../../docs/benchmark-data-quality.md)。
