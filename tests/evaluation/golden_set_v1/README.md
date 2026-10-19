# Golden Set V1 — 真实语料候选标注工作包

本目录是 **候选标注**，不是已审核的 ground truth。全部 27 条检索候选与 3 条
无答案候选均由 AI 助手起草，`review_status = DRAFT_UNVERIFIED`，
`scorable = false`。评测门禁在 `benchmarks/golden_set_contract.py` 与
`benchmarks/annotation.py` 中拒绝它们，直到**真实人员**完成审核。

## 文件

| 文件 | 条数 | 说明 |
|---|---:|---|
| `candidates_v1.jsonl` | 27 | 检索候选，每条携带真实 `(doc_id, chunk_id)` |
| `no_answer_candidates_v1.jsonl` | 3 | 语料无法回答的问题，仅用于 answer-gate 评测 |
| `candidate_validation.json` | 1 | 本次自动校验结果（结构、ID、审核状态） |
| `retrieval_review_workpackage.jsonl` | 27 | **人工审核工作表**：问题、来源定位、原文、ID、当前等级、派生风险、遗漏段落线索、空白决定 |
| `RETRIEVAL_REVIEW_WORKPACKAGE.md` | — | 上表的人读版，逐条展开 |
| `answer_gate_review_workpackage.jsonl` | 3 | Answer Gate 审核表，**不进入 Retrieval Recall 分母** |
| `ANSWER_GATE_REVIEW.md` | — | 上表的人读版，附 near-miss 段落与语义判断问题 |
| `independent_query_proposals.jsonl` | 6 | 独立编写的用户式查询提案，`in_dataset=false`，不参与指标 |
| `INDEPENDENT_QUERY_PROPOSALS.md` | — | 上表的人读版 |

语料与索引绑定：

- `corpus_version` = `real-v1-20261019`
- `corpus_sha256` = `es_fingerprint.json` 中真实 ES 索引的 `content_sha256`

## 覆盖类别

| 类别 | 条数 | 说明 |
|---|---:|---|
| `regulation_precise` | 9 | 精确法规条文检索 |
| `ingredient_query` | 8 | 原料/INCI/历史使用量检索 |
| `semantic_paraphrase` | 3 | 同义改写 |
| `multi_document` | 4 | 需要多段证据 |
| `hard_negative` | 3 | 相似但不相关的困难负例 |
| `no_answer` | 3 | 语料无法回答，应拒答 |

## 当前实测状态

- 结构审计（`benchmarks.annotation.audit_dataset`）：**27/27 结构有效，0 可评分**。
- 契约校验（`golden-set-contract/v2`）：**0/27 通过**，唯一原因是
  `annotation_not_reviewed:DRAFT_UNVERIFIED` 与 `missing_annotation_reviewed_by`
  （契约对未审核记录本就不给 `reviewed_by`）。
- ES 存在性解析：27 条候选的全部 `(doc_id, chunk_id)` 均能在
  `cosmetics_docs` 索引（`corpus_version=real-v1-20261019`）中解析到，**没有一条**
  报 `unresolved`。
- 语料/索引绑定校验（`scripts/corpus/verify_real_corpus_v1_binding.py`，只读）：
  源文件哈希、本地语料指纹、候选绑定、索引 content fingerprint、epoch/文档数、
  标注可解析性全部 `PASS`；`trust_state` 为 `FAIL`（现有索引仍是旧代码写入的
  `MANAGED_INTERNAL`），`config_epoch` 为 `FAIL`
  （`knowledge_version_epoch=default` ≠ `real-v1-20261019`）。
- 来源信任：两份源文件为 `UNTRUSTED / PENDING_REVIEW`，**已审批 0/2**。在生产
  索引重建前必须完成具名审批。

复现命令（需要本机 ES；本仓库不提交 ES 数据）：

```bash
# 1) 重建语料切块（不需要服务）
python3 scripts/corpus/build_real_corpus_v1.py

# 2) 只读预检：不写、不删，报告索引状态、epoch、trust 类别与冲突
ELASTICSEARCH_HOST=http://127.0.0.1:9200 \
  python3 scripts/corpus/index_real_corpus_v1_es.py --check-only

# 2b) 工程验证用隔离索引（不是可激活快照；来源未审批时需 --staging-unapproved）
ELASTICSEARCH_HOST=http://127.0.0.1:9200 \
  python3 scripts/corpus/index_real_corpus_v1_es.py \
  --profile experiment --index cosmetics_docs_exp_v1 \
  --staging-unapproved --out artifacts/corpus/cosmetics_docs_exp_v1.json

# 2c) 生产索引只在具名来源审批后重建；必须显式确认索引名
ELASTICSEARCH_HOST=http://127.0.0.1:9200 \
  python3 scripts/corpus/index_real_corpus_v1_es.py \
  --profile production --recreate --confirm-index cosmetics_docs

# 3) 重建候选与人工审核工作包
python3 scripts/validation/build_real_golden_set_v1.py
python3 scripts/validation/build_real_golden_set_v1_workpackage.py

# 3b) 校验语料、候选与实时索引是否一致（只读）
python3 scripts/corpus/verify_real_corpus_v1_binding.py

# 4) 结构审计
python3 scripts/validation/audit_annotations.py --dataset tests/evaluation/golden_set_v1/candidates_v1.jsonl

# 5) 契约 + ES 存在性解析（会因未审核而 exit 1，但不应出现 unresolved）
CONFIG_PATH=<指向本机 ES 的 config.json> \
python3 scripts/validation/validate_golden_set_contract.py \
  --dataset tests/evaluation/golden_set_v1/candidates_v1.jsonl --resolve-es
```

## 人工审核流程（不可由 AI 代办）

**先做来源审批。** 两份源文件是外部官方文档，当前 `UNTRUSTED / PENDING_REVIEW`。
在具名审批之前，生产索引写入会被拒绝：

```bash
python3 run_offline.py review-source corpus/real_v1/sources/regulation_727.jsonl \
  --source-id corpus/real_v1/sources/regulation_727.jsonl \
  --approve --actor <具名审核人> --note "<审批依据>"
```

`used_ingredients_2021.jsonl` 同样处理。审批绑定文件字节哈希；改动文件会使其失效。

1. 打开 `RETRIEVAL_REVIEW_WORKPACKAGE.md`，阅读每条候选的 `question`，对照
   `annotations[].text` 与索引中的原文，确认 `(doc_id, chunk_id)` 指向的段落确实
   支撑该问题；检查 `possibly_missing_passages` 中的线索是否需要补入。
2. 修正错误的映射、补充遗漏段落、删除不相关段落；把 `relevance` 设为
   `2` 高度相关 / `1` 部分相关 / `0` 已考虑但排除。
3. 审核通过后，由**真实人员**执行提升，二选一：
   - **保留 AI 贡献**：设置 `reviewed_by`、`reviewed_at` 与 `promoted_by`（见
     `benchmarks.annotation.promote_to_reviewed`）；
   - **转为人工标注**：把 `annotation.annotator` 改为本人、`source` 改为
     `human`，再由**另一位**人员设置 `reviewed_by`（`reviewed_by` 必须不同于
     `annotator`）。
4. 提升后可评分的记录写入 `goldenset-annotation/v1` 数据集，并重新运行
   `audit_annotations.py`；只有 `scorable > 0` 的 reviewed 记录才允许进入评测。

`reviewed_by == annotator` 会被拒绝；没有具名审核人的记录无法通过
`audit_annotations.py`。请勿为了跑通 benchmark 而伪造审核。

## 已知限制

- 三类 `no_answer` 样本按设计**不参与检索指标**；它们用于 answer-gate 评测。
  缺席性只相对本语料成立，不是一般性法律结论。
- 候选的 `difficulty` / `complexity_label` 由 AI 初判，人工审核时应重新确认。
- 本目录的候选文件不是 `annotations.schema.json` 的最终数据集；提升后的记录
  才写入 `tests/evaluation/golden_set_v2/annotations_v1.jsonl` 或新的 V1 正式集。
- 不包含 `visual_required = true` 样本：本 V1 语料为纯文本，无法验证视觉路径。
