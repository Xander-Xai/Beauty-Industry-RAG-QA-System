# REAL_CORPUS_V1_PRE_REVIEW_REPORT

本文件是 PR #111 定向复核（安全索引重建、来源信任契约、Golden Set 审核准备、
正式评测边界）的结论记录。它记录**实测**状态，不是审核结论，也不提升任何样本。

- 分支：`feat/real-corpus-golden-set-v1`
- 复核基线 HEAD：`f80e1138bd60bb455b431409a28648a7ef375b3e`（本轮修正提交：`347c617`）
- PR：#111（保持 Draft，未合并）
- 语料：`real-v1-20261019`，209 逻辑文档 / 212 chunks

## 1. ES 索引重建安全性结论

### 复核发现（修正前）

`scripts/corpus/index_real_corpus_v1_es.py` 无条件调用
`writer.ensure_index(recreate=True, confirm=True)`。任何一次执行都会删除并重建
生产索引名 `cosmetics_docs`；`confirm=True` 是脚本自己写死的，不是操作者授权。
索引名固定为生产名，没有实验/生产隔离，也没有冲突预检。

### 修正后的契约

| 要求 | 实现 |
|---|---|
| 不默认销毁/覆盖 | 已存在的非空索引在无显式动作时被拒绝，且**不删除任何数据** |
| 重建需明确授权 | `--recreate` 必须同时提供 `--confirm-index <确切索引名>` |
| 实验/生产隔离 | `--profile production` 只接受 `cosmetics_docs`；`--profile experiment` 只接受 `cosmetics_docs_exp_*` |
| 检查名称/epoch/已有数据/指纹 | `--check-only` 只读输出索引名、文档数、epoch 桶、trust_class 计数、content_sha256 |
| 冲突默认拒绝 | 已有非空索引、epoch 已存在、指纹路径冲突均 fail-closed |
| 非破坏性回归测试 | `tests/test_real_corpus_v1_safety.py`（24 项） |
| 不实际删除现有索引 | 本轮**未**删除、未重建生产索引 `cosmetics_docs`；其文档数前后均为 212 |

本轮实际写入的只有一个**新建的实验索引** `cosmetics_docs_exp_v1`
（`--profile experiment --staging-unapproved`，212 docs，provenance 全部为
`UNTRUSTED / PENDING_REVIEW`）。它不覆盖生产索引，也不是可激活快照。生产索引
`cosmetics_docs` 的 212 个文档在操作前后未变。

实测预检（只读，未改动索引）：

```
documents=212  epochs=['real-v1-20261019']
trust_classes={'MANAGED_INTERNAL': 212}
content_sha256=38849be7fe8ce077ea5a5e61fa1fa3909143b8a9088dd55d372b3d1c64711a1c
plan.action=refused  ("Nothing was deleted.")
```

### 同类问题（已一并修正）

`scripts/validation/validate_es_auth.py` 把 `cosmetics_docs` 当作验证用临时索引，
并在结束时 `indices.delete(index=TEST_INDEX)`。在真实语料主机上运行该验证会直接
删除语料索引。已改为 `cosmetics_docs_validation`，并在常量等于生产索引时拒绝运行。

### 未决点

现有索引仍是**旧代码写入的** `MANAGED_INTERNAL` provenance。它不能被当前索引器
再产出，因此 `verify_real_corpus_v1_binding.py` 将 `trust_state` 判为 `FAIL`。
**本轮不删除它**；是否重建由人决定，且必须先完成第 2 节的来源审批。

## 2. 来源信任分类与审批状态

### 复核结论

外部官方文档**不能**按现有契约声明为 `MANAGED_INTERNAL / NOT_REQUIRED`。
`offline/source_trust.py` 的定义是：`MANAGED_INTERNAL` 指“在受管数据根目录内
产生并维护的内容”；两份文件来自政府网站与 NMPA 公告附件，属于外部导入。

修正前有两处违规声明：

1. `scripts/corpus/build_real_corpus_v1.py::_provenance` → `MANAGED_INTERNAL / NOT_REQUIRED`
2. `scripts/corpus/index_real_corpus_v1_es.py` → 每个 chunk 同样声明
   `MANAGED_INTERNAL / NOT_REQUIRED`

这等于让外部内容零审核进入可激活快照，正是 trust contract 要阻止的绕过。

### 修正后

| 项 | 值 |
|---|---|
| `source_trust` | `UNTRUSTED` |
| `approval_status` | `PENDING_REVIEW` |
| `trust_class` | `UNTRUSTED` |
| 审批单元 | `corpus/real_v1/sources/*.jsonl` 的**文件字节哈希** |
| 审批入口 | 既有 `run_offline.py review-source --approve --actor <具名审核人>` |
| 当前已审批来源 | **0 / 2** |
| 伪造审核人 | 无；本轮未写入任何 approval 记录 |

生产索引写入在来源未审批时被拒绝；实验索引可在 `--staging-unapproved` 下暂存，
但携带 `UNTRUSTED` provenance，明确不是可激活快照。

### 引用与再分发边界

- 法规正文：依《著作权法》第五条第一项不适用著作权法；仅提交派生摘录。
- 原料目录：官方规范性文件，提交事实性派生字段。
- **INCI 名称可能涉及第三方来源**（目录说明第三条指向 PCPC 词典）。本仓库不复制
  词典解释性文本，但“事实性字段是否构成实质性复制”**不作绝对性法律结论**，按
  许可不确定记录。详见 `corpus/real_v1/SOURCES.md`。

## 3. 原有语料与索引证据是否保持有效

| 检查 | 结果 |
|---|---|
| 源文件 SHA-256 与 `manifest.json` 一致 | PASS（2/2） |
| `chunks.jsonl` 重算 `corpus_sha256_local` | PASS（`dfc22916…`） |
| 27 条候选 `corpus_version` / `corpus_sha256` 绑定 | PASS |
| 实时索引 content fingerprint 与 `es_fingerprint.json` 一致 | PASS（`38849be7…`） |
| 实时索引 epoch / 文档数与 manifest 一致 | PASS（212 / 212） |
| 27 条候选的 `(doc_id, chunk_id)` 全部可解析 | PASS（0 unresolved） |
| 实时索引 provenance 类别 | **FAIL**：仍为 `MANAGED_INTERNAL`（旧代码产物） |
| `config.json::knowledge_version_epoch` 与语料 epoch | **FAIL**：`default` ≠ `real-v1-20261019` |

**结论**：语料内容与指纹证据**仍然有效**（字节级可复现，候选绑定未漂移）；
索引的**provenance 与检索 epoch 配置**不满足当前契约，属于待人工处理项，
不是语料内容损坏。

复现：

```bash
python3 scripts/corpus/verify_real_corpus_v1_binding.py
```

## 4. 27 条候选的可审核工作包位置

| 文件 | 内容 |
|---|---|
| `tests/evaluation/golden_set_v1/RETRIEVAL_REVIEW_WORKPACKAGE.md` | 人读版，逐条 27 条 |
| `tests/evaluation/golden_set_v1/retrieval_review_workpackage.jsonl` | 机读版，同样 27 条 |

每条包含：`sample_id`、问题、官方来源与原文定位、原始段落、`doc_id` / `chunk_id`、
当前 relevance、difficulty / complexity_label、可能遗漏的其他相关段落、待审核决定、
人工审核备注（空）、审核人和审核日期（空）。

**问题派生偏差检查**：27 条问题全部由语料起草。工作包用词面启发式标注风险：

| 风险 | 条数 | 含义 |
|---|---:|---|
| `HIGH` | 2 | 问题与段落共享长连续子串或几乎全部 bigram，可能直接改写自段落 |
| `MEDIUM` | 8 | 部分重叠，建议改写成真实用户问法后再接受 |
| `LOW` | 17 | 词面重叠较低 |

`HIGH` 不代表一定是缺陷，只要求审核人复核措辞。该启发式**不修改任何标签**。
另附 6 条独立编写的用户式查询提案：
`tests/evaluation/golden_set_v1/INDEPENDENT_QUERY_PROPOSALS.md`
（`in_dataset=false`，不参与任何指标）。

## 5. 3 条无答案候选的审核位置

| 文件 | 内容 |
|---|---|
| `tests/evaluation/golden_set_v1/ANSWER_GATE_REVIEW.md` | 人读版 |
| `tests/evaluation/golden_set_v1/answer_gate_review_workpackage.jsonl` | 机读版 |

3 条样本**单独成表**，`evaluation_target=answer_gate`，
`excluded_from_retrieval_recall=true`，**不进入 Retrieval Recall 分母**。

工作包明确写出：关键词缺失只是**词面**证据，不能判定语义上不可回答；并附
`near_miss_passages`（含被搜索物质词但无数值的段落）供人工判断。审核人须回答
“换个说法是否可回答”“是超出本语料范围还是普遍不可回答”。

## 6. 现有探索性 BM25 指标的适用边界

本轮在真实 ES 上重跑了一次**探索性** BM25（`--executor real --config bm25`，
27 条未审核草稿标签）：

| 指标 | 值 |
|---|---:|
| Recall@1 / @3 / @5 / @10 | 0.500 / 0.648 / 0.759 / 0.852 |
| HitRate@1 / @3 / @5 / @10 | 0.556 / 0.704 / 0.815 / 0.889 |
| MRR@10 | 0.653 |
| NDCG@10 | 0.677 |
| 延迟 mean / p50 / p95 / p99（ms） | 37.0 / 12.8 / 23.6 / 479.9 |

冷启动与稳态必须分开：

| 口径 | n | mean | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|---:|
| 全部（含首查询） | 27 | 37.03 | 12.79 | 23.60 | 479.92 | 640.12 |
| 冷启动（第 1 次查询） | 1 | 640.12 | — | — | — | 640.12 |
| 稳态（第 2 次起） | 26 | 13.84 | 12.66 | 22.14 | 23.66 | 23.96 |

**适用边界（不可越界引用）**：

1. **标签未经人工审核**：全部 `DRAFT_UNVERIFIED`，`scorable=false`。
2. **问题可能存在语料内生成偏差**：27 条问题由语料起草；2 条词面高度重叠。
3. **小样本**：27 条，不能代表生产质量，不构成 SLO。
4. **Cold Start 与稳态延迟不同口径**：p99 479.9ms 由单次冷启动主导；稳态 p99
   23.7ms。混算会同时误报两者。
5. **Dense / Hybrid 尚未执行**：BGE 权重缺失，配置为 `BLOCKED`，无任何数字。
6. **不是 Benchmark**：artifact 中 `results_are_benchmark=false`、`attributable=false`。

## 7. 测试、CI、PR HEAD

| 项 | 状态 |
|---|---|
| 新增回归测试 | `tests/test_real_corpus_v1_safety.py`（24 项） |
| 原有语料测试 | `tests/test_real_corpus_v1.py`（5 项） |
| Ruff | PASS |
| 公开仓库边界检查 | PASS |
| 全量测试 | `3290 passed, 16 skipped`（排除 `e2e_remote` / `runtime` / `load`） |
| 专项测试 | 语料 5 项 + 安全 24 项 + 边界 122 项 + 一致性 610 项，全部通过 |
| PR HEAD | `347c617`（CI 全绿：Ruff / 测试 3.10 / 3.11 / 边界 / 确定性守卫 / pip-audit） |
| PR 状态 | **Draft**，未合并，未关闭 #86 / #18 |

## 8. 是否允许进入真实人工审核阶段

**允许**，且范围限定为：

- 审核 27 条检索候选（第 4 节文件）。
- 审核 3 条 Answer Gate 候选（第 5 节文件）。
- 决定 2 份源文件的来源审批（第 2 节流程）。

**不允许**：

- 在来源审批完成前重建生产索引并把它当作可激活快照。
- 在人工审核完成前执行正式发布流程，或把 27 条草稿指标改写成 Benchmark。
- 由 AI 填写审核人、审核日期或来源审批结论。

正式门禁（**人工审核完成且环境具备后**）才执行：

```bash
python3 -m benchmarks.retrieval_benchmark \
  --executor real \
  --config bm25 \
  --dataset <真实已审核文件路径> \
  --require-contract \
  --require-integrity \
  --require-results \
  --output-dir artifacts/benchmarks
```

## 9. 需要你本人完成的具体审核动作

1. **来源审批（先做）**：对
   `corpus/real_v1/sources/regulation_727.jsonl` 与
   `corpus/real_v1/sources/used_ingredients_2021.jsonl` 逐份确认来源与许可，
   然后由你本人执行：

   ```bash
   python3 run_offline.py review-source corpus/real_v1/sources/regulation_727.jsonl \
     --source-id corpus/real_v1/sources/regulation_727.jsonl \
     --approve --actor <你的名字> --note "<审批依据>"
   ```

   `used_ingredients_2021.jsonl` 同样处理。**不要**让我代填 actor。

2. **逐条审核 27 条检索候选**：按
   `tests/evaluation/golden_set_v1/RETRIEVAL_REVIEW_WORKPACKAGE.md` 填写
   `decision` / `reviewer` / `reviewed_at` / `notes`；对 `HIGH` / `MEDIUM` 风险
   条目决定是否改写问题措辞。

3. **逐条审核 3 条 Answer Gate 候选**：按
   `tests/evaluation/golden_set_v1/ANSWER_GATE_REVIEW.md` 判断语义可回答性。

4. **决定 6 条独立查询提案**：按
   `tests/evaluation/golden_set_v1/INDEPENDENT_QUERY_PROPOSALS.md` 选择
   `ADOPT_LATER` / `REJECT`。

5. **决定索引与 epoch**：现有索引是旧 provenance，且
   `config.json::knowledge_version_epoch=default` 与语料 epoch 不一致。是否重建
   生产索引、何时把检索 epoch 切到 `real-v1-20261019`，由你在来源审批后决定。

6. **具名提升**：只有你完成审核后，才可按 `benchmarks/annotation.py`
   `promote_to_reviewed` 把记录提升为 `REVIEWED`。在此之前，任何
   `review_status` 都不会被自动提升。
