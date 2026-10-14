# 集成测试查询集 — 合成夹具

`queries.json` 是 `tests/integration/` 冒烟测试的**输入查询集**，不是真实语料。

## 合成声明

文件中的法规名称与成分限量问题是**虚构占位内容**，未经任何监管机构或第三方标准原文校对。
其中「烟酰胺的安全浓度」「化妆品中铅含量的限量标准」「水杨酸在驻留型产品中的最大允许浓度」
等条目沿用与 `tests/evaluation/golden_set.jsonl` 相同的虚构限量，**不得**用于合规判定、
配方开发或成分限用判断。

这些查询存在的唯一目的，是让端到端链路以**形状真实**的输入跑通检索、重排、
Evidence Gate 与 Answer Gate。

完整边界见 [`DATA_PROVENANCE.md`](../../../DATA_PROVENANCE.md) 第 4 节。

## 替换为真实语料时

同步更新 `DATA_MANIFEST.json` 中该条目的 `class` 与 `carries_real_regulatory_claims`，
以及 `DATA_PROVENANCE.md` 第 2 节的清单。
`scripts/check_public_repository_boundary.py` 会强制三者一致。
