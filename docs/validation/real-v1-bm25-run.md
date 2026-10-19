# 真实语料 V1 — BM25 检索实测运行报告

> **这不是已完全放行的正式 Benchmark。** 数据集是真人签署、契约全通过的正式金标集，
> 检索是真实 BM25，但**来源审批只完成 1/2**：所有者明确只批准了
> `regulation_727.jsonl`，`used_ingredients_2021.jsonl` 仍为 `PENDING_REVIEW`，
> 因此本索引以 `--staging-unapproved` 构建，其中 130 篇文档的 provenance 为
> `UNTRUSTED`。27 条中有 **13 条**的金标段落来自该未批准来源。
> 详见 §6。

---

## 1. 运行标识与归因

| 项 | 值 |
|---|---|
| `run_id` | `20261019T160921Z-f1cda59b` |
| Git SHA | `227eac9de81f2f4033776d67eb1598cfc631ca1d` |
| `git_dirty` | **false**（干净工作树，未使用 `--allow-dirty`） |
| 配置 | `bm25`，`--executor real`，`--top-k 10` |
| 门禁参数 | `--require-contract --require-integrity --require-results` |
| 退出码 | 0 |
| `synthetic_retriever` | **false**（真实 `BM25Retriever`，非替身） |

### 数据与环境指纹

| 项 | 值 |
|---|---|
| 已签署金标集 | `tests/evaluation/golden_set_v1/reviewed/annotations_v1_reviewed.jsonl` |
| 数据集 SHA-256 | `9621d150996f9144d7d4b8af486ec5f4cccfbf29dc3a31599b82c328349159d1` |
| 候选集 SHA-256 | `7bfd5ac239f269833531a9b80f615ab615ba77cfb4930aa8b889ce608243f81d` |
| 签署台账 SHA-256 | `755026a7278d0fc851d3d9b5f0239141aee7c6e3c5e1b595e59c222c8292b35e` |
| ES 索引 | `cosmetics_docs_exp_v2`（隔离实验索引，未触碰 `cosmetics_docs`） |
| 索引 content_sha256 | `38849be7fe8ce077ea5a5e61fa1fa3909143b8a9088dd55d372b3d1c64711a1c` |
| 索引文档数 | 212（`doc_version_epoch = real-v1-20261019`） |
| 金标段落对应 | **21/21 命中，ratio 1.0，verdict full** |
| `knowledge_version_epoch` | `real-v1-20261019`（临时 `CONFIG_PATH` 对齐，未修改 `config.json`） |

### Artifact SHA-256

| 文件 | SHA-256 |
|---|---|
| `metadata.json` | `b00759f8ad6ea12122286c9a3016ca71533cad57964904225b23f9b1e3443c3d` |
| `retrieval_metrics.json` | `9354c8a398fbbb36b6be72ebd229e49b236765ce7e662f52090251b13287cd3f` |
| `latency_metrics.json` | `f37600ae65c26e155e72d4b14b32553d9b7475c0875003ec23bebb7833d912ee` |
| `per_query_results.jsonl` | `e1aa7331e75f9c46b3dcadb5b7917c441ff1fd27bfd9e866ddd8bdee625b6359` |
| `environment.json` | `a18e6e9f82267a535cafafe69e363cc1911c1a561f791a7973cc5969fd43686c` |
| `report.md` | `3cc811ec336570699a7e78738dec895c7be43298e1a703e4f62a5c63c9460c76` |

按 `artifacts/benchmarks/README.md` 的约定，运行产物本身**不提交仓库**，此处只记录其
指纹与内容摘要。

---

## 2. 门禁核验

| 门禁 | 结果 | 证据 |
|---|---|---|
| 人工签署 | ✅ | 27/27 `REVIEWED`，`reviewed_by=Xander-Xai`，`reviewed_at=2026-10-19` |
| 自审 | ✅ 无 | `reviewed_by != annotator`（`llm:opencode-deepseek-v4.1-flash`） |
| LLM 来源提升留痕 | ✅ | 27/27 `promoted_by=Xander-Xai` |
| Golden Set Contract v2 | ✅ **27/27 valid，0 invalid** | `dataset_contract.valid_count=27` |
| 数据完整性 | ✅ | `dataset_integrity_errors: none` |
| V1 派生 Schema | ✅ | 仅 `sample_id` 模式与 V2 不同；同一批记录用**未修改的 V2 schema** 经单射映射复验通过 |
| 金标可解析 | ✅ | 21/21 独立段落全部命中索引与语料，文本逐字一致 |
| 真实 ES（非 mock） | ✅ | `bm25 available=true`，`corpus available=true` |
| 干净工作树 | ✅ | `git_dirty=false`，Git SHA 可归因 |
| **来源审批 2/2** | ❌ **1/2** | 见 §6 |

---

## 3. 检索指标（n = 27，27 条全部评分，completion_rate = 1.0）

| 指标 | 值 |
|---|---|
| Recall@1 / @3 / @5 / @10 | **0.2963 / 0.4074 / 0.5185 / 0.7037** |
| HitRate@1 / @3 / @5 / @10 | **0.3333 / 0.4074 / 0.5185 / 0.7037** |
| MRR@10 | **0.4088** |
| NDCG@10 | **0.4698** |

### 延迟

| 口径 | 值 |
|---|---|
| 冷启动（首条 / 最大值） | **1072.3 ms** |
| 稳态均值 | 69.45 ms |
| p50 / p90 / p95 | 23.84 / 62.32 / 125.48 ms |

### 分桶

| 难度 | n | R@1 | R@10 | MRR@10 | NDCG@10 |
|---|---:|---:|---:|---:|---:|
| easy | 14 | 0.179 | 0.714 | 0.314 | 0.398 |
| medium | 12 | 0.458 | 0.667 | 0.526 | 0.551 |
| hard | 1 | 0.000 | 1.000 | 0.333 | 0.500 |

| 业务类型 | n | R@1 | R@10 | MRR@10 | NDCG@10 |
|---|---:|---:|---:|---:|---:|
| regulation | 14 | 0.429 | 0.714 | 0.558 | 0.583 |
| ingredient | 13 | 0.154 | 0.692 | 0.248 | 0.348 |

---

## 4. 失败样本（8 条，金标未进入 top-10）

| # | 问题 | 首个命中名次 |
|---:|---|---|
| 0 | 根据《化妆品监督管理条例》，化妆品的法定定义是什么？ | 未命中 |
| 2 | 化妆品新原料在什么情况下需要注册，什么情况下只需备案？ | 未命中 |
| 11 | …泛醇在驻留类产品中的 2021 年版记载最高历史使用量是多少？ | 未命中 |
| 14 | …水杨酸的最高历史使用量当时是否给出了具体数值？备注栏写的是什么？ | 未命中 |
| 17 | 《化妆品监督管理条例》是怎么定义化妆品的？ | 未命中 |
| 20 | 企业使用已列入目录的原料生产国产普通化妆品，需要办理哪些注册和备案手续？ | 未命中 |
| 24 | …苯甲醇在驻留类产品中的最高历史使用量当时是否给出了数值？… | 未命中 |
| 26 | …对苯二胺的备注栏当时写的是什么？ | 未命中 |

全部 27 条均返回了检索结果（**0 条零召回执行**），失败均为排序问题，不是执行故障。

### 错误归因

**REAL-V1-020（对应正式金标 `REAL-V1-021`）** 是本轮已知的真实失败：BM25 **从未**把
第十七条召回到 top-10。在上一版候选集上该样本曾显示为 0.50 的 top-10 召回，那是
第四条（rank 3）被召回造成的假象——第四条并不参与该问题的作答。移除这条不参与作答的
金标后，真实值是 **0.00**，即完全未命中。

其余 7 条的共同根因是**中文无分词**：`cosmetics_docs_exp_v2` 使用默认 `standard`
分析器，长查询被切成单字 token（36 字查询 → 34 个 token），问题越长信号被稀释得
越厉害。实测问题长度与召回率负相关：

| 问题长度 | n | 平均 Recall@10 |
|---|---:|---:|
| ≤25 字 | 6 | **0.833** |
| 26–35 字 | 8 | 0.750 |
| >35 字 | 13 | **0.615** |

10 条原料问题都带约 24 字的固定版本限定前缀
（"根据《已使用化妆品原料目录（2021年版）》，"）——这是把问题钉在 2021 快照上的
**正确**做法，但在无分词的 BM25 下同时构成查询噪声。本轮 6 条未命中里有 5 条属于
这一类。

---

## 5. 指标字段的准确含义

本次 artifact 中 `results_are_benchmark = true`、`attributable = true`、
`data_class = REVIEWED_GOLDEN_SET`。需要明确的是：**该字段反映的是"数据集可归因"**
（真人签署、契约通过、无 mock 检索器），**不代表来源审批门禁已全部通过**。
来源门禁是独立的、人工判断的一环，见 §6。因此本报告不把这次运行称为"已完全放行的
正式 Benchmark"。

---

## 6. 未通过的门禁：来源审批 1/2

| 来源 | SHA-256 | TrustRegistry | 索引内 provenance |
|---|---|---|---|
| `regulation_727.jsonl` | `c9499ca5…` | **APPROVED**（actor `Xander-Xai`，2026-10-19） | `APPROVED_EXTERNAL`（82 篇） |
| `used_ingredients_2021.jsonl` | `3dd67722…` | **PENDING_REVIEW** | `UNTRUSTED`（130 篇） |

审批台账 `data/offline_source_trust.sqlite3` 中只有一条决定，绑定 `c9499ca5…` 这批
字节。所有者已被明确告知 `--approve` 意味着可进入**可激活**信任状态（不只是实验标签），
且 `SOURCES.md` 中的历史版本限制不会自动变成生产访问限制；所有者据此选择**只批准条例**。

**直接后果（已记录，未回避）**：

1. 索引器在不使用 `--staging-unapproved` 时**拒绝**构建该索引——
   `refused: 1 source(s) are unapproved`。因此 `cosmetics_docs_exp_v2` 是
   **混合 provenance 的暂存实验索引，不是可激活快照**，不能作为生产依赖。
2. **27 条金标中有 13 条**（`REAL-V1-010`～`017`、`020`、`023`～`025`、`027`）
   完全依赖未批准的目录来源；其召回数字建立在一个所有者尚未批准进入可激活状态的来源上。
3. 因此"正式 Benchmark 成功"这一结论**不成立**。真实状态是：真人签署完成、
   BM25 实测完成且可复现，但来源门禁未清。

---

## 7. 本报告数字的适用边界

- **样本量 27**，且语料为固定 2021 历史快照，不是统计显著的结果集。
- **BM25 单路**，未启用 dense / hybrid；BGE 权重缺失，`dense` 与 `hybrid_rrf` 仍为
  `model_assets_unavailable`，**未生成任何 Mock 数字**。
- **本机单节点 ES 8.11.0**，延迟数字只反映本地回环网络，**不代表生产服务性能**。
- **语料是 2021 快照**：条例第七十八条过渡期已于 2025-12-31 届满；NMPA 2025 年第61号
  公告已删除"最高历史使用量"栏。这批数字**不代表现行法规问答准确率**。
- 13/27 样本的证据来自**未批准**的来源（§6）。

---

## 8. 解除阻塞所需的最小动作

1. 若所有者希望 BM25 成为完全放行的正式 Benchmark：批准
   `used_ingredients_2021.jsonl`（绑定 `3dd67722…`），重新以非暂存方式构建隔离索引，
   重跑本命令。本报告 §1 的全部命令可直接复用。
2. 若不批准该来源：接受本报告 §3 的数字为**"来源未完全审批下的真实实测"**，
   而非正式 Benchmark。13 条原料样本的数字应视为临时证据。
3. 独立于审批问题：为 BM25 索引接入中文分词分析器（IK / IK_SMART / smartcn）后重跑，
   预计 §4 的 7 条长查询失败中有相当部分可被修复。这需要重建索引，应作为单独决策。

Refs #18. Refs #86.
