# Real RAG Evaluation Bootstrap Report — Golden Set V1

本报告记录把仓库从 `ROUTINE_GOVERNANCE_CLOSED` 推进到 `REAL_RETRIEVAL_EVALUATION`
这一轮（Issue #86 / #18）实际完成的语料准备、候选标注与确定性验证。它只记录实测结果，
不把计划写成结论。

## 1. 当前仓库与运行环境

| 项 | 值 |
|---|---|
| 基线 `main` SHA | `9c61fd629d3c569a257be214e178af89c8ebe517` |
| 工作分支 | `feat/real-corpus-golden-set-v1` |
| 工作树 | 干净（改动全部在独立分支） |
| Open PR（开始时） | 无 |
| Python / Torch / CUDA | 3.10.12 / 2.12.1+cu130 / RTX 5060 Ti 16GB 可用 |
| 磁盘 | 790G 可用 |
| Docker | 29.5.2 |
| Elasticsearch | **可用**：本机 `127.0.0.1:9200`，ES 8.11.0（仓库已有镜像） |
| Qdrant | 未启动；镜像 `qdrant/qdrant:latest` 存在，但 dense 还缺权重，未建 collection |
| BGE 权重 | **缺失**：`./models/bge-base-zh-v1.5` 不存在，未自动下载 |
| CrossEncoder / NLI / CLIP | 缺失，按 #18 范围排除 |

结论：BM25 路径可以真实执行；Dense / Hybrid 因缺少 BGE 权重不具备条件。

## 2. 候选语料及许可证据

选择范围最小、来源可追溯的两份中国官方文件：

| 来源 | 发布者 | 版本 | 许可依据 | 能否公开再分发 |
|---|---|---|---|---|
| 《化妆品监督管理条例》 | 国务院 | 国令第727号，2021-01-01 施行 | 《著作权法》第五条第（一）项 | 允许 |
| 《已使用化妆品原料目录（2021年版）》 | NMPA | 2021年第62号公告 | 《著作权法》第五条第（一）项 | 允许 |

完整 URL、获取时间、原始文件 SHA-256、允许的使用范围、残余风险（INCI 名称来源、
"最高历史使用量≠法规限量"、版本时效）见 [`corpus/real_v1/SOURCES.md`](../../corpus/real_v1/SOURCES.md)。
**没有**使用前公司非公开数据、个人职业材料或不明许可语料；未提交原始 `.xlsx` / HTML，
只提交派生摘录。

## 3. 已取得并允许使用的数据

- `corpus/real_v1/sources/regulation_727.jsonl`：81 条（1 条公布信息 + 80 条正文）。
- `corpus/real_v1/sources/used_ingredients_2021.jsonl`：128 条（1 条目录说明 + 127 条
  精选原料行，精确中文名匹配，非全文目录）。
- 原始文件 SHA-256：xlsx `25d2218…`，HTML `38b9521…`。

## 4. 实际完成的语料处理与索引步骤

1. `scripts/corpus/extract_official_sources_v1.py` 从官方文件解析出规范化源记录。
2. `scripts/corpus/build_real_corpus_v1.py` 使用仓库自有的
   `offline/document_processor.DocumentProcessor`（chunk 500 / overlap 50）生成
   `doc_id` / `chunk_id`，输出 `chunks.jsonl` 与 `manifest.json`。
   - 209 份逻辑文档，212 个 chunk。
   - 本地指纹 `corpus_sha256_local = dfc2291673ae…`。
3. `scripts/corpus/index_real_corpus_v1_es.py` 用生产版
   `offline/elasticsearch_writer.ElasticsearchWriter` 写入 ES 索引 `cosmetics_docs`
   （epoch `real-v1-20261019`），并用 `benchmarks.corpus.fingerprint_elasticsearch`
   生成索引指纹，写入 `es_fingerprint.json`。
   - `corpus_sha256 = 38849be7fe8ce077…`，209 个文档 / 212 个 point。
   - **未写 dense 向量**：无 BGE 权重，因此不声称 Qdrant/向量索引已通过。
4. `scripts/validation/build_real_golden_set_v1.py` 把每条候选的 `(doc_id, chunk_id)`
   解析到真实 chunk，解析失败即报错；无一条凭空写入 ID。
5. `tests/test_real_corpus_v1.py` 回归验证：切块可复现、每个标注可解析、候选全部为
   `DRAFT_UNVERIFIED`、指纹与标注绑定一致、无答案候选确实无匹配 chunk。

没有通过复制 Golden Set 现成答案到索引制造命中：语料只来自两份官方文档，候选问题
从语料起草。

## 5. Golden Set 候选数、审核数、有效数

| 指标 | 数值 |
|---|---:|
| 检索候选 | 27 |
| 无答案候选（answer-gate） | 3 |
| 已由真实人员审核（`REVIEWED`） | **0** |
| 结构有效草稿（`validate_annotation`） | 27 / 27 |
| 可评分（`scorable`） | **0** |
| v2 契约通过 | 0 / 27 |

分类覆盖：`regulation_precise` 9、`ingredient_query` 8、`semantic_paraphrase` 3、
`multi_document` 4、`hard_negative` 3，另有 `no_answer` 3。
契约不通过的**唯一**原因是 `annotation_not_reviewed:DRAFT_UNVERIFIED` 与
`missing_annotation_reviewed_by`，不是数据或 ID 错误。**未伪造任何审核人。**

## 6. 已验证的 doc_id / chunk_id 映射

```
CONFIG_PATH=<本机 ES config> python3 scripts/validation/validate_golden_set_contract.py \
  --dataset tests/evaluation/golden_set_v1/candidates_v1.jsonl --resolve-es
```

实测：

```
total:   27
valid:   0
invalid: 27
invalid reasons: {"missing_annotation_reviewed_by": 27, "annotation_not_reviewed:DRAFT_UNVERIFIED": 27}
```

`invalid_reasons` 中**没有** `annotation_*_unresolved`，即全部标注的
`(doc_id, chunk_id, corpus_version)` 都能在真实 ES 索引中解析到。基准运行时的语料
probe 也独立确认：`all 22/22 distinct golden passages are present in the configured
corpus`，语料对应率 `1.0`。

## 7. 真实 Benchmark 运行结果或阻塞原因

### 7.1 官方命令（按任务书要求）

```bash
python3 -m benchmarks.retrieval_benchmark \
  --executor real --configs bm25,dense,hybrid_rrf \
  --dataset tests/evaluation/golden_set_v1/candidates_v1.jsonl \
  --require-contract --require-integrity --require-results \
  --output-dir artifacts/benchmarks
```

结果：**`BLOCKED`，exit 2，未写 artifact**，原因：

```
0/27 samples satisfy the golden-set-contract/v2 contract
(reasons: {'missing_annotation_reviewed_by': 27, 'annotation_not_reviewed:DRAFT_UNVERIFIED': 27})
```

这是正确行为：在真实人员审核之前，没有可发布的 Recall/MRR/NDCG。

### 7.2 探索性 BM25（真实 ES，明确非 benchmark）

在官方门禁之外，单独用真实生产检索路径跑了一次 **探索性、小样本、未审核标签** 的
BM25，仅用于验证语料可检索、映射可用。产物写明
`results_are_benchmark = false`、`attributable = false`、`synthetic_retriever = false`、
`corpus.verdict = full`。

| 指标 | BM25（27 条草稿，探索性） |
|---|---:|
| Recall@1 / @3 / @5 / @10 | 0.500 / 0.648 / 0.759 / 0.852 |
| HitRate@1 / @3 / @5 / @10 | 0.556 / 0.704 / 0.815 / 0.889 |
| MRR@10 | 0.653 |
| NDCG@10 | 0.677 |
| 检索延迟 mean / p50 / p95 / p99 (ms) | 215.0 / 11.5 / 47.7 / 4054.1 |

- 这些数字**不是**可发布基准：标签是 `DRAFT_UNVERIFIED`，样本仅 27 条，且
  ES 默认分析器对中文分词并不理想（部分法规条文 query 未进前 10），因此数值偏保守、
  不代表最终系统质量。
- **Dense / Hybrid：`BLOCKED`**，原因 `model_assets_unavailable`
  （`./models/bge-base-zh-v1.5` 不存在），未用 Mock 生成数字。

## 8. 实际代码、数据和 PR 变更

- 新增脚本：`scripts/corpus/{extract_official_sources_v1,build_real_corpus_v1,index_real_corpus_v1_es}.py`、
  `scripts/validation/build_real_golden_set_v1.py`。
- 新增数据：`corpus/real_v1/`（sources、chunks、manifest、ES 指纹、SOURCES.md）。
- 新增候选与文档：`tests/evaluation/golden_set_v1/`（candidates、no-answer、validation、README）。
- 新增回归测试：`tests/test_real_corpus_v1.py`。
- 数据契约更新：`DATA_MANIFEST.json` 增加 `PUBLIC_OFFICIAL_DOCUMENT` / `CANDIDATE`
  两类与 8 个资产；`DATA_PROVENANCE.md` 增加 4.1 节与清单行；
  `tests/test_check_public_repository_boundary.py` 改为要求"真实法规主张"必须由
  `PUBLIC_OFFICIAL_DOCUMENT` + 来源标记支撑。
- 全量测试：`3266 passed, 17 skipped`；边界检查与一致性检查均通过。
- 变更在独立分支 `feat/real-corpus-golden-set-v1`，**未直接改 main、未自动合并**。

## 9. 需要人工参与的最小工作

1. **审核 27 条检索候选**：逐条对照 `annotations[].text` 与
   `corpus/real_v1/chunks.jsonl`，确认/修正段落映射与 `relevance`；
   重新确认 `difficulty` 与 `complexity_label`。
2. **审核 3 条无答案候选**：确认语料确实无法支撑，或补入相应语料后改写。
3. **具名提升**：真实人员按 `benchmarks/annotation.py::promote_to_reviewed` 设置
   `reviewed_by` / `reviewed_at` / `promoted_by`；若改为人工标注，`reviewed_by` 必须
   不同于 `annotator`。AI 不能冒充审核人。
4. **（可选）补 BGE 权重**：由授权人员提供本地权重后，增量构建 Qdrant collection，
   才能解除 Dense / Hybrid 的 `BLOCKED`。

## 10. 下一步唯一最有价值的执行任务

**由一名真实人员完成 27 条候选的逐条审核并具名提升为 `REVIEWED`。**
这是当前唯一同时解锁 #86 验收条件与 #18 官方 benchmark 的动作；它不需要模型、GPU
或外部服务，只需人的判断。提升后即可用第 7.1 节命令得到第一份**可发布**的
BM25 artifact；Dense / Hybrid 待 BGE 权重到位后再补。
