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
| `review_decisions_v1.jsonl` | 27 | **空白人工签署台账**，已绑定每条候选的 `candidate_sha256` |
| `ai_review_v1.jsonl` | 38 | **机器可读 AI 预审记录**（`review_type=AI_REVIEW`），与人工台账物理分离 |

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
- ES 存在性解析：27 条候选的全部 `(doc_id, chunk_id)` 均能在索引中解析到，
  **没有一条**报 `unresolved`（21 个不重复金标段落全部命中，ratio = 1.0）。
- 语料/索引绑定校验（`scripts/corpus/verify_real_corpus_v1_binding.py`，只读）：
  `source_bytes` / `local_corpus` / `candidate_binding` / `index_identity` /
  `index_content` 全部 `PASS`；`trust_state` 与 `config_epoch` 为 `FAIL`
  （见下）。
- 来源信任：两份源文件为 `UNTRUSTED / PENDING_REVIEW`，**已审批 0/2**。
- 签署台账：`review_decisions_v1.jsonl` 为**空白模板**，**已签署 0/27**。
- AI 预审记录：`ai_review_v1.jsonl`（38 行，`review_type=AI_REVIEW`）+ `AI_REVIEW.md`。
  逐条结论与证据见 `AI_REVIEW.md`；**AI 结论不是人工签署，不改变任何 `review_status`**。

### 探索性 BM25 实测（**不可发布**）

`results_are_benchmark=false`、`review_type=AI_REVIEW`、`publication_status=EXPLORATORY_ONLY`、
`data_class=CANDIDATE`、`attributable=false`、`git_dirty=false`，
git SHA `8d882aa`，索引 `cosmetics_docs_exp_v1`，
索引指纹 `38849be7fe8ce…`，`doc_version_epoch=real-v1-20261019`，212 文档，
21/21 不重复金标段落全部命中（ratio 1.0）。

| 指标 | AI 修正后（当前候选集） | AI 修正前（`dd3680b` 候选集） |
|---|---|---|
| Recall@1 / @3 / @5 / @10 | **0.296 / 0.407 / 0.519 / 0.704** | 0.296 / 0.426 / 0.537 / 0.722 |
| HitRate@1 / @3 / @5 / @10 | **0.333 / 0.407 / 0.519 / 0.704** | 0.333 / 0.444 / 0.556 / 0.741 |
| MRR@10 | **0.409** | 0.421 |
| NDCG@10 | **0.470** | 0.477 |
| 延迟（冷启/最大值、稳态均值、p50、p95） | 702.0 ms、42.3 ms、15.5 ms、28.2 ms | 972.1 ms、48.8 ms、12.9 ms、21.5 ms |

**这是 27 条 AI 草稿、固定历史语料快照上的小样本探索结果，不代表现行法规问答准确率，
也不代表生产性能。** 3 条 answer-gate 样本不参与这些指标。
**两份候选集的相关集不同，指标不可跨版本横向比较**——差异只来自 REAL-V1-021 的
金标变更（第四条 rel=1 → 移出），检索器、索引与查询均未改变。

> **差异来源（已逐条核对）**：两版指标只有 **REAL-V1-021** 一行不同
> （`Recall@10 0.50 → 0.00`，`MRR 0.333 → 0.000`）。BM25 **从未把第十七条召回到
> top-10**；修正前该样本之所以"对了一半"，只是因为第四条被召回在 rank 3，
> 而第四条并非答案所在段落。移除这条不参与作答的金标后，一次此前被假金标
> 掩盖的检索失败变得可见。指标下降的原因是标注变对，不是标注变错。
> 详见 `AI_REVIEW.md` §2。

> **口径提醒**：更早 PR 正文公布的 `Recall@10 0.852 / MRR@10 0.653 / NDCG@10 0.677`
> 对应 **628a244 重新分级之前**的候选集（已用 `git show 39afa0e:` 复现，数值逐位一致）。
> 那是三个不同候选标签版本中的第三个，同样**不可**与上表横向比较。

### 已实测确认的两个检索事实

1. **`config_epoch` FAIL 会让 BM25 返回零行。** 提交的 `config.json` 中
   `knowledge_version_epoch = "default"`，而索引内文档的 `doc_version_epoch` 是
   `real-v1-20261019`。`retrieval/bm25_retriever.py` 把该字段作为 `term` 过滤器下推，
   按提交配置查询该索引会返回 0 条（首次运行即复现：`retrieved_items` 27/27 为空、
   全指标 0.0）。绑定校验器早已以明文判定该情况。上表数字是在**临时 `CONFIG_PATH`**
   下取得的。
2. **索引无中文分词，长查询被稀释。** 默认 `standard` 分析器把 36 字查询切成
   34 个单字 token。问题长度与召回率负相关：≤25 字 0.833（n=6）、26–35 字 0.750（n=8）、
   >35 字 0.615（n=13）。这属于索引配置决策，需重建索引才能验证，
   **不在本 PR 范围内**（本轮不修改 `cosmetics_docs`）。

复现方式：

```bash
python3 - <<'EOF'
import json, pathlib
c = json.load(open('config.json'))
c['elasticsearch'] = {"host": "http://127.0.0.1:9200", "index": "cosmetics_docs_exp_v1",
                      "enabled": True, "username": "", "password": ""}
c['knowledge_version_epoch'] = "real-v1-20261019"   # 与索引内 doc_version_epoch 对齐
pathlib.Path('/tmp/config_es_exp.json').write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding='utf-8')
EOF
CONFIG_PATH=/tmp/config_es_exp.json python3 -m benchmarks.retrieval_benchmark \
  --config bm25 --executor real \
  --dataset tests/evaluation/golden_set_v1/candidates_v1.jsonl --top-k 10 \
  --output-dir /tmp/bench_exp
```

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

## 签署 → 正式数据集的导入链路

工作表是给人看的，正式数据集是给评测用的。两者之间原本没有程序化通道，
`import_real_v1_review_decisions.py` 补上了这一段。它不新增审核平台，只读取
已经存在的文件、写入已经有归属的目录。

### 三条物理隔离规则

1. **AI 预审与人工签署是两个文件。** `retrieval_review_workpackage.jsonl` 带
   `ai_suggestion` / `ai_correction`，由生成器随时重建；`review_decisions_v1.jsonl`
   只放人写的字段，任何生成器都不会改写它。导入器**拒绝**读取带 `ai_*` 字段的
   决定行，机器建议无法被当成签署。
2. **决定绑定它审核过的那份字节。** 每条决定带 `candidate_sha256` 与
   `candidate_dataset_sha256`。候选集若在审核开始后被重建，绑定失效，导入失败，
   而不是把决定套用到已经不同的文本上。
3. **没有真名和日期就不提升。** 导入器没有 `--reviewer`，没有 `--reviewed-at`，
   也没有任何默认审核人。决定为空 → 保持 `DRAFT_UNVERIFIED`；缺审核人、缺日期、
   缺 `llm_candidate` 必需的 `promoted_by`、审核人等于原作者 → **报错退出**，
   而不是悄悄降级成草稿。日期只读取，不生成。

### 用法

```bash
# 1) 生成空白台账（已存在的已签署台账不会被覆盖）
python3 scripts/validation/import_real_v1_review_decisions.py \
  --emit-template tests/evaluation/golden_set_v1/review_decisions_v1.jsonl

# 2) 人工填 decision / reviewer / reviewed_at（llm_candidate 行还需 promoted_by）

# 3) 预演：只报告，不写任何文件
python3 scripts/validation/import_real_v1_review_decisions.py

# 4) 写入正式数据集
python3 scripts/validation/import_real_v1_review_decisions.py --apply
```

### 输出（`tests/evaluation/golden_set_v1/reviewed/`，不提交仓库）

| 文件 | 内容 |
|---|---|
| `annotations_v1_reviewed.jsonl` | 已签署并提升的 `REVIEWED` 记录，**唯一可评分的数据集** |
| `annotations_v1_drafts.jsonl` | 未签署或被拒绝导入的记录，保持 `DRAFT_UNVERIFIED` |
| `annotations_v1_rejected.jsonl` | 被 `REJECT` 的候选，**永不进入评分集**，但保留可审计 |
| `sample_id_map_v2.jsonl` | `REAL-V1-001` → `0001` 的无损映射 |
| `annotations_v1.schema.json` | 从 V2 派生的 V1 正式 schema |
| `import_report.json` | 计数、哈希、拒绝原因 |

### V1 / V2 schema 兼容：不是放宽，是派生

V2 schema 把 `sample_id` 钉死为 `^[0-9]{4}$`，因为它的编号是某份源金标集的**行号**；
V1 用的是工作表标识 `REAL-V1-001`。把它改写成四位数字会切断记录与审核人实际签署的
那一行之间的唯一联系。

因此导入器**从 V2 schema 派生** V1 schema，只改 `sample_id` 这一处，
`required`、`additionalProperties: false`、`REVIEWED` 条件与全部枚举逐字继承。
`assert_schema_not_relaxed()` 把两者的差异与一个**精确的允许集合**比对：
出现任何预期之外的差异就报错退出；缺少应有差异（说明根本没派生）同样报错。
测试里有一条专门把 V1 的 `allOf` 删掉，验证这个检查确实会失败——它不是装饰。

映射 `REAL-V1-007 → 0007` 因 `^REAL-V1-[0-9]{3}$` 限定 000–999 而**单射**，
测试对真实提升过的记录同时用**未经修改的 V2 schema** 复验一遍内容：
如果 V1 在别处被放松过，这一步会失败。

### 生成器不会吃掉人工决定

- `build_real_golden_set_v1.py` 早已拒绝覆盖带人工审核字段的候选文件。
- `build_real_golden_set_v1_workpackage.py` 现在**同样**拒绝重建已被签署的工作表
  （本次新增）。此前它每次都无条件重写并清空 `review` 字段——审核人在工作表上
  写下决定后，任何人重跑一次生成器就会静默抹掉唯一一份人工记录。

## 多金标与 relevance 的评分口径（复核 `benchmarks/metrics.py` 后订正）

以下是对评分口径的**解释**，不是对 `benchmarks/metrics.py` 的修改。指标实现本身
不在本目录的改动范围内。

**1. 多金标时 `Recall@K` 的理论上限就是 `1/n`，这是正常定义。**
`GRADE_THRESHOLD_RELEVANT = 1`（`benchmarks/models.py`），因此 `relevance >= 1` 的
段落全部进入二值 Recall 的相关集，`recall_at_k = |relevant ∩ top-K| / |relevant|`。
一条样本有 2 个金标时，完美检索的 `Recall@1` 上限即为 **0.5**，`Recall@10` 只有
两个金标都进前 10 才能到 1.0。这是 TREC 多相关文档判定的标准口径，**不是污染，
也不是需要修正的对象**。

**2. 不要求 27 条样本具有相同数量的金标。**
指标按样本各自的相关集计算再取宏平均；金标条数不同的样本，其 Recall 量纲本就不同，
但只要每条样本的相关集标注正确，跨样本平均值仍然有意义。要求"统一金标数量"会把
标注口径和指标口径混为一谈。

**3. 判断是否多标，依据是该段落能否独立支撑原问题，而不是"额外细节多寡"。**
逐条自问：**只检索到这一段，读者能否完整回答原问题？**
- 能 → `relevance = 2`（高度相关）
- 能回答问题的一部分，但不足以完整作答 → `relevance = 1`（部分相关）
- 不能支撑该问题的作答 → 不标注，或 `relevance = 0`（已考虑并排除）

**4. 比较系统强弱时，必须在同一检索结果列表下比较，不能跨口径对比。**
把"系统 A 在单金标口径下的 1.000"和"系统 B 在双金标口径下的 0.760"放在一起没有
意义——两者的相关集不同。正确做法是对每个候选口径，把**所有**系统在同一口径下重跑，
再横向比较。指标实现已经按此设计：`HitRate` 与 `MRR` 只看首个命中（MRR 对多金标
只增不减），`NDCG` 消费完整分级。

**5. `relevance = 1` 与 `2` 的统一语义（避免凭"细节多少"打分）。**
| 值 | 语义 | 判据 |
|---|---|---|
| `2` highly_relevant | **可直接、完整地支撑答案** | 该段单独足以回答原问题 |
| `1` partially_relevant | **提供答案所需的另一部分** | 单独不足以完整回答，但与 2 级段落合起来构成完整答案 |
| `0` not_relevant | 已考虑并排除 | 主题相近但不能支撑该问题的作答 |

`1` 级是"**缺了它答案就不完整**"，不是"信息更多"。一段话信息量更大但回答的是
别的子问题，仍应是 `0`。

**6. 本节不改变任何已有的人工审核决定。**
候选的 `relevance` 调整只能由具名人工审核人通过既有工作包流程作出。

## 已知限制

- 三类 `no_answer` 样本按设计**不参与检索指标**；它们用于 answer-gate 评测。
  缺席性只相对本语料成立，不是一般性法律结论。
- 候选的 `difficulty` / `complexity_label` 由 AI 初判，人工审核时应重新确认。
- 本目录的候选文件不是 `annotations.schema.json` 的最终数据集；经
  `import_real_v1_review_decisions.py` 签署导入后，记录落在
  `tests/evaluation/golden_set_v1/reviewed/annotations_v1_reviewed.jsonl`
  （V1 正式集，schema 从 V2 派生）。该目录由台账重建，不提交仓库。
- 不包含 `visual_required = true` 样本：本 V1 语料为纯文本，无法验证视觉路径。
