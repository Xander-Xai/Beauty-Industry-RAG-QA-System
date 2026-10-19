# 真实语料 V1 — 全审批索引上的正式 BM25 运行报告

> **这是本仓库目前唯一一次通过全部发布门禁的检索 Benchmark。**
> 上一版 [`real-v1-bm25-run.md`](real-v1-bm25-run.md) 的"来源审批 1/2"结论
> 保持原样，不回填；本报告是那之后的一次全新运行。

---

## 1. 运行标识与归因

| 项 | 值 |
|---|---|
| `run_id` | `20261019T190215Z-b679c73e` |
| Git SHA | `c8e49e2db6383fd23c09a78c4408c1e9cbcd1166` |
| `git_dirty` | **false**（干净工作树，未使用 `--allow-dirty`） |
| 配置 | `bm25`，`--executor real`，`--top-k 10` |
| 门禁参数 | `--require-contract --require-integrity --require-results --require-source-approval` |
| 退出码 | **0** |
| `synthetic_retriever` | **false**（真实 `BM25Retriever`，非替身） |
| `config_sha256` | `b3fa48e3a1cec55d…` |
| `sample_ids_hash` | `45eebe8cea6a2288…`（四次运行一致，证明样本集未变） |

### 数据、索引与环境

| 项 | 值 |
|---|---|
| 已签署金标集 | `tests/evaluation/golden_set_v1/reviewed/annotations_v1_reviewed.jsonl` |
| 数据集 SHA-256 | `9621d150996f9144d7d4b8af486ec5f4cccfbf29dc3a31599b82c328349159d1` |
| ES 索引 | `cosmetics_docs_exp_v3_approved`（隔离实验索引；**未触碰** `cosmetics_docs`） |
| ES **服务端**版本 | `8.11.0`（单节点，本机回环；未安装任何插件） |
| ES **客户端**库版本 | `8.19.3`（`environment.json` 记录的是这个，勿与服务端混淆） |
| 索引 `content_sha256`（语料内容指纹） | `38849be7fe8ce077ea5a5e61fa1fa3909143b8a9088dd55d372b3d1c64711a1c` |
| 索引逐篇身份指纹（本轮新算） | `dd5719442674fa1b678b465b35c08904a32984a2ad0f24c4cbe5bbdd42d598f6` |
| 索引文档数 | **212**（`doc_version_epoch = real-v1-20261019`，唯一 epoch） |
| `knowledge_version_epoch` | `real-v1-20261019`（独立 `CONFIG_PATH` 对齐，**未修改** `config.json`） |
| 分析器 | `standard`（索引默认，未声明 `--analyzer`） |
| 金标段落对应 | **21/21 命中，ratio 1.0，verdict `full`** |

### 来源审批（本轮的核心）

| 源文件 | 字节 SHA-256 | TrustRegistry | 索引内 provenance | 文档数 |
|---|---|---|---|---:|
| `regulation_727.jsonl` | `c9499ca5…5c91` | **APPROVED**（`Xander-Xai`，2026-10-19T15:45:47Z，revision 1） | `APPROVED_EXTERNAL` | 82 |
| `used_ingredients_2021.jsonl` | `3dd67722…0c06b` | **APPROVED**（`Xander-Xai`，2026-10-20T02:20:59Z，revision 1） | `APPROVED_EXTERNAL` | 130 |

- `PENDING_REVIEW` 文档数：**0**
- `UNTRUSTED` 文档数：**0**
- `staging_unapproved`：**false**（构建时未使用 `--staging-unapproved`）
- 审批台账可读：**true**
- 逐篇核验：**212/212**
- 金标段落归属已审批来源：**21/21**

---

## 2. 门禁核验

| 门禁 | 结果 | 证据字段 |
|---|---|---|
| 人工签署 | ✅ | 27/27 `REVIEWED`，`reviewed_by=Xander-Xai`，`reviewed_at=2026-10-19` |
| 自审 | ✅ 无 | `reviewed_by != annotator`（`llm:opencode-deepseek-v4.1-flash`） |
| Golden Set Contract v2 | ✅ **27/27 valid** | `dataset_contract.valid_count=27`, `invalid_count=0` |
| 数据完整性 | ✅ | `dataset_integrity_errors: []` |
| 金标可解析 | ✅ | `source_approval.gold_chunk_resolved = 21/21` |
| Gold Chunk 原文一致 | ✅ | 21 段逐字比对，0 处不一致 |
| 语料指纹绑定 | ✅ | 金标 `corpus_sha256` 与索引 `content_sha256` 均为 `38849be7…` |
| 真实 ES（非 mock） | ✅ | `synthetic_retriever=false`；`--executor real` |
| 干净工作树 | ✅ | `git_dirty=false` |
| **来源审批 2/2** | ✅ **2/2** | `source_approval.verified=true`，`issues: {}` |
| `results_are_benchmark` | ✅ **true** | |
| `publishable` | ✅ **true** | `publication_blockers: []` |

### 来源审批核验做了什么

`benchmarks/source_approval.py` 读取的是**本次运行实际检索的那个索引**，
逐篇（212/212，非 top-N 抽样）核对：

- `provenance.source_id` / `source_trust` / `approval_status` / `trust_class`
- `approval_actor` 非空、`approved_content_hash` 非空
- `trust_class` 与 `(source_trust, approval_status)` 的派生值自洽
- 索引内的 `approved_content_hash` == 台账记录的 `content_hash` == **磁盘源文件当前字节的重新哈希**
- epoch 唯一且等于 `real-v1-20261019`
- `staging_unapproved` 未置位
- 21 个 Gold Chunk 全部落在已审批来源内

任何一条不满足即 `verified=false`。台账不可读、索引不可达、只读到部分文档，
同样 `verified=false` —— fail-closed，没有"拿不到证据就默认通过"的分支。

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
| 冷启动（首条 / 最大值） | **5521.6 ms** |
| 最小 / p50 / p90 / p95 | 8.74 / 13.66 / 17.71 / 18.34 ms |
| 均值 | 217.59 ms（被冷启动单条拉高） |

> 延迟为本机回环单节点 ES 8.11.0 的实测值，**不代表生产服务性能**。均值 217.59 ms
> 几乎完全由 5.5 秒的冷启动首条决定；稳态 p95 为 18.34 ms。

### 分桶

| 业务类型 | n | R@1 | R@10 | MRR@10 | NDCG@10 |
|---|---:|---:|---:|---:|---:|
| regulation | 14 | 0.429 | 0.714 | 0.558 | 0.583 |
| ingredient | 13 | 0.154 | 0.692 | 0.248 | 0.348 |

| 难度 | n | R@1 | R@10 | MRR@10 | NDCG@10 |
|---|---:|---:|---:|---:|---:|
| easy | 14 | 0.179 | 0.714 | 0.314 | 0.398 |
| medium | 12 | 0.458 | 0.667 | 0.526 | 0.551 |
| hard | 1 | 0.000 | 1.000 | 0.333 | 0.500 |

**0 条零召回执行失败**；问题全部是排序问题，不是执行故障。

---

## 4. 失败样本（8 条，金标未进入 top-10）

| # | 样本 | 问题 | 首个命中名次 |
|---:|---|---|---|
| 0 | REAL-V1-001 | 根据《化妆品监督管理条例》，化妆品的法定定义是什么？ | 未命中 |
| 2 | REAL-V1-003 | 化妆品新原料在什么情况下需要注册，什么情况下只需备案？ | 未命中 |
| 11 | REAL-V1-012 | …泛醇在驻留类产品中的 2021 年版记载最高历史使用量是多少？ | 未命中 |
| 14 | REAL-V1-015 | …水杨酸的最高历史使用量当时是否给出了具体数值？备注栏写的是什么？ | 未命中 |
| 17 | REAL-V1-018 | 《化妆品监督管理条例》是怎么定义化妆品的？ | 未命中 |
| 20 | REAL-V1-021 | 企业使用已列入目录的原料生产国产普通化妆品，需要办理哪些注册和备案手续？ | 未命中 |
| 24 | REAL-V1-025 | …苯甲醇在驻留类产品中的最高历史使用量当时是否给出了数值？… | 未命中 |
| 26 | REAL-V1-027 | …对苯二胺的备注栏当时写的是什么？ | 未命中 |

**REAL-V1-021** 是上一版报告里已知的真实失败（BM25 从未把它召回到 top-10）。
在本报告的 `standard` 基线上**它仍然失败**；中文分词 A/B 修复了它，
见 [`chinese-analyzer-ab.md`](chinese-analyzer-ab.md) §4。

---

## 5. 与上一版运行的数字为何完全一样

上一版（PR #113）报告的数字是 `R@10 = 0.7037`、`MRR@10 = 0.4088`，
与本报告**逐位相同**。这不是复制，是预期结果：

两次运行的语料字节**完全一致** —— `cosmetics_docs_exp_v2` 与
`cosmetics_docs_exp_v3_approved` 的 `content_sha256` 都是 `38849be7…`，
即同一批 212 个 chunk、同样的正文、同样的 chunk_id。改变的是这些字节的
**审批状态**，不是可检索内容。

- 上一版：129 篇 `APPROVED_EXTERNAL` + 83 篇 `UNTRUSTED`（混合），以
  `--staging-unapproved` 构建；
- 本版：212 篇全部 `APPROVED_EXTERNAL`，非暂存构建。

因此"数字没变"恰好是正确的信号：如果审批状态的变化让指标动了，那才说明
被测量的东西变了，说明实验控制出了问题。

---

## 6. 证据与可复现性

原始运行产物按仓库既有规则**不提交**
（[`artifacts/benchmarks/README.md`](../../artifacts/benchmarks/README.md)）。
取而代之提交的是**可重算**的证据包：

| 文件 | 内容 |
|---|---|
| [`docs/validation/evidence/real-v1-approved-bm25-evidence.json`](evidence/real-v1-approved-bm25-evidence.json) | 门禁标志、全部指标、延迟、来源审批、索引身份、原始 artifact 哈希 |
| [`docs/validation/evidence/real-v1-approved-bm25-per-sample.jsonl`](evidence/real-v1-approved-bm25-per-sample.jsonl) | 27 条逐样本：Gold Chunk ID、召回 Chunk ID、首个命中名次、逐臂指标 |
| [`docs/validation/evidence/chinese-analyzer-tokenisation.json`](evidence/chinese-analyzer-tokenisation.json) | 各分析器 `_analyze` 真实分词输出 |
| `scripts/validation/verify_benchmark_evidence.py` | 从逐样本证据重算全部指标 |

第三方无需 ES、无需模型权重、无需网络即可核验：

```bash
python3 scripts/validation/verify_benchmark_evidence.py
```

```
All published metrics reproduced from committed evidence.
```

该脚本走的是仓库自己的 `benchmarks.metrics.score_ranking` 与
`aggregate_results`，因此是对 artifact 的独立复算，而不是同一段算术的再抄一遍。
篡改任一已发布数字或任一逐样本名次都会被检出并指名，见
`tests/validation/test_benchmark_evidence_package.py`。

### 本次运行的原始 artifact 指纹（供本地核对，文件未提交）

| 文件 | SHA-256 |
|---|---|
| `metadata.json` | `c534f3911379af9bd8809aae9c80cbb5391953465724bc79aa51029c5ecc769a` |
| `retrieval_metrics.json` | `f2e07ebba9488578b4dc76dac9e71e67f9caaf174aaca28b9d603ce65edb7824` |
| `latency_metrics.json` | `401fa1a2328dbe54c5800bd107c14d9836655baecd1cc698566cf02bfc94e99d` |
| `per_query_results.jsonl` | `be7629cdfa7bd6b948039271db9f69f2f3d12c9b958f38692d8c12021d1a87ac` |
| `environment.json` | `4007c0b2ce2152531cf96c1b772fc9370ca573a622abf5c35eebdad0bb2f7362` |
| `report.md` | `4b402aad544cf409b9b33b5ca13622902137598d6566f92989e89ccadec49920` |

### 未公开的部分与理由

| 未公开内容 | 理由 |
|---|---|
| `data/offline_source_trust.sqlite3`（原始审批台账） | 含审批主体与内部路径；台账的**已发布投影**在 evidence JSON 中，运行时仍逐条重新核验台账与磁盘字节 |
| `artifacts/benchmarks/<run-id>/*`（原始产物） | 本地运行输出，按既有规则不提交；其**内容摘要 + 哈希 + 可重算替代**已提交 |
| 索引内官方文档正文 | 属官方法规派生摘录，许可依据见 `corpus/real_v1/SOURCES.md`；逐样本证据只含 chunk ID 与名次，不含正文 |

---

## 7. 复现命令

```bash
# 1. 确认两份来源已审批（原始台账不在仓库，需本地重建审批）
python3 run_offline.py review-source corpus/real_v1/sources/regulation_727.jsonl \
  --source-id corpus/real_v1/sources/regulation_727.jsonl --list

# 2. 构建全审批隔离索引（不触碰 cosmetics_docs）
ELASTICSEARCH_HOST=http://127.0.0.1:9200 \
python3 scripts/corpus/index_real_corpus_v1_es.py \
  --profile experiment --index cosmetics_docs_exp_v3_approved \
  --approval-store data/offline_source_trust.sqlite3 \
  --out artifacts/corpus/cosmetics_docs_exp_v3_approved.json

# 3. 用独立 CONFIG_PATH 指向该索引与 epoch（不改 config.json）
#    elasticsearch.host=http://127.0.0.1:9200
#    elasticsearch.index=cosmetics_docs_exp_v3_approved
#    knowledge_version_epoch=real-v1-20261019

# 4. 运行
python3 -m benchmarks.retrieval_benchmark \
  --executor real --config bm25 \
  --dataset tests/evaluation/golden_set_v1/reviewed/annotations_v1_reviewed.jsonl \
  --top-k 10 --require-contract --require-integrity --require-results \
  --require-source-approval --output-dir artifacts/benchmarks

# 5. 核验已发布证据
python3 scripts/validation/verify_benchmark_evidence.py
```

---

## 8. 本报告数字的适用边界

- **样本量 27**，且语料为固定 2021 历史快照。**不是统计显著的结果集**，
  不支持显著性推断。
- **单路 BM25**。未启用 dense / hybrid：BGE 权重缺失，`dense` 与 `hybrid_rrf`
  仍为 `model_assets_unavailable`，**未生成任何 Mock 数字**。
- **单节点本机 ES 8.11.0**，延迟只反映本地回环，不代表生产性能。
- **语料是 2021 快照**：条例第七十八条过渡期已于 2025-12-31 届满；NMPA 2025
  年第 61 号公告已删除"最高历史使用量"栏。**这些数字不代表现行法规问答准确率。**
- **`source_approval_verified=true` 只表示语料通过来源审批门禁**，
  不表示内容是现行有效的监管依据，也不表示可用于生产。
  五项条件的区分见
  [`production-index-governance.md`](production-index-governance.md) §1。

Refs #18. Refs #86.
