# 离线训练数据说明 — 合成夹具

`finetune_data.json` 是 `offline/finetune_qlora.py` 的**训练样例夹具**，
用于验证微调脚本的数据加载、模板化与训练循环能跑通，不是真实语料。

## 合成声明

文件中的法规名称（如《化妆品安全技术规范》《化妆品监督管理条例》《化妆品标签管理办法》、
《化妆品功效宣称评价规范》《化妆品生产质量管理规范》）与配方、防腐体系、
注册备案流程等内容为**人工构造的占位文本**，成分浓度与限量数值为**虚构值**，
未经任何监管机构或第三方标准原文校对。

其中包含对不设限量的常见功效性原料给出"最大允许浓度"式数值的情形，
以及以倍数表述的虚构功效数据。这些内容**仅用于让数据管线有一条形状真实的输入**。

**不得**将其训练出的模型用于合规判定、配方开发、成分限用判断或任何面向消费者的答复。

完整边界见 [`DATA_PROVENANCE.md`](../../DATA_PROVENANCE.md) 第 4 节。

## 替换为真实语料时

1. 确认语料来源与再分发许可，来源无法确认的材料先进入
   `offline/source_trust.py` 的 `UNTRUSTED` 隔离区，人工批准后才可激活。
2. 替换 `finetune_data.json` 的内容，同时更新 `DATA_MANIFEST.json` 中该条目的
   `class` 与 `carries_real_regulatory_claims` 字段。
3. 更新 `DATA_PROVENANCE.md` 第 2 节的清单与第 4 节的声明。
4. `scripts/check_public_repository_boundary.py` 会强制以上三者一致。
