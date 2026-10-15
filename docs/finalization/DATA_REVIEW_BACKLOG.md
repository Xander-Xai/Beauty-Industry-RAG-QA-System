# 人工核验清单 — 数据来源与许可

**状态：待处理。本文件列出本仓库中许可来源或技术正确性无法由自动检查确认的材料。**

自动扫描能确认"某段文本不含密钥"或"某个文件被标注为合成"，但**不能**确认一段法规
表述在法律或技术意义上正确，也不能确认一段材料的再分发许可。以下条目必须由具备
资质的人判断。

完整的边界声明见 [`DATA_PROVENANCE.md`](../../DATA_PROVENANCE.md)。

---

## 1. 合成法规内容的技术正确性（最高优先级）

**问题**：仓库中的合成数据集以真实法规名称为引用来源，但配以虚构的成分浓度、
限量和标准号。这些内容用于驱动检索与门控链路，本身不是安全评估依据，但**具有被
误当作真实知识转述的风险**。

**为什么不能自动解决**：逐条比对需要监管机构或第三方标准原文，而这些原文的
再分发许可本身不明确。

### 1.1 本轮已处置：混合形态最重的一处

`data/mock_generator.py`（随仓库发布的源码）此前生成：

- 标题形如 `国家标准 - GB/T 4823-2019 化妆品相关标准`、`QB/T`、`NMPA`、`HFJB`、
  `EU`、`FDA`、`ASEAN` 的法规文档；
- 条款编号、标准版式（`1 范围` / `2 规范性引用文件` / `4 技术要求`）；
- **真实 INCI 名称 + 真实 CAS 编号 + 虚构限量**（烟酰胺 ≤5%、视黄醇 ≤0.3%、
  铅 ≤10 mg/kg、汞 ≤1 mg/kg …）。

单看一个生成的 `.txt`，它与真实技术标准不可区分。它是本仓库中"真实标识 +
虚构数值"耦合最紧、且**不在数据清单内**（因为它是 `.py`）的一处——此前的披露机制
（`DATA_MANIFEST.json` + sidecar）按数据扩展名枚举，扫不到生成器。

**已执行的处置**（改标识，不改数值）：

- 标准系列改为自造标识 `DEMO-STD-*` / `DEMO-GRP-*` / `DEMO-REQ-*` / `DEMO-ANN-*` /
  `DEMO-REG-*`，与 `docs/demo/synthetic_corpus.json` 使用同一套虚构系列；
- 原料改为 `DEMO-ACT-*` 编号，CAS 改为保留占位符 `000-00-0`，重量元素改为
  `DEMO-TRACE-*` 示踪物；
- 每个生成的 `.txt` 以合成声明开头；`metadata.jsonl` / `ingredients.jsonl` /
  `formulas.jsonl` 每条记录带 `data_class: "SYNTHETIC"` 与
  `carries_real_regulatory_claims: false`；输出目录旁写出 `DATA_PROVENANCE.md`；
- `scripts/check_public_repository_boundary.py` 新增第 6 项检查
  `fabricated_regulatory_authority_violations`，在 CI 中拒绝合成生成器源码中出现
  真实法规标识（单条即报错），以及"真实法规标识 + 限量数值"的组合（作用域限定为
  生成器类源码，见脚本内注释）；
- 披露只在**散文**（注释）里成立。以数据字段形式携带的标记（`"data_class":
  "SYNTHETIC"`）是被生成器写进产物的元数据，不能为同一条记录背书；
- `tests/test_mock_generator.py` 固定披露契约，`tests/test_check_public_repository_boundary.py`
  固定该检查的正例与反例，包含"跨行拆分"与"元数据伪装披露"两种绕过。

工程有效性未受影响：`tests/test_mock_generator.py` 原有 4 项结构断言全部保留并通过
（字段 `inci_name` 因改名为 `act_code` 而同步更新，结构键 `ingredient_id` /
`safety_info` / `role_mask` 未变）。

### 1.2 逐文件实测清单（混合形态到底在哪里）

下表由脚本对每个跟踪数据文件逐行统计得出，统计口径与
`DATA_MANIFEST.json` 的 `regulatory_shaped_fields` 一致：*"引用真实法规标识"*
指出现 `《…》` 或 `化妆品安全技术规范` / `化妆品监督管理条例` 等法规名；
*"含限量数值"*指出现 `≤X%` / `不超过 X%` / `X mg/kg` / `X ppm` 形态。

| 文件 | 记录数 | 引用真实法规 | 含限量 | 两者兼有 | 带法规形态的字段 |
|---|---:|---:|---:|---:|---|
| `tests/evaluation/golden_set.jsonl` | 301 | 110 | 116 | 51 | `question` `contexts[]` `answer` `ground_truth` |
| `tests/evaluation/golden_set_expanded.jsonl` | 301 | 104 | 111 | — | 同上 |
| `tests/evaluation/sample_golden_set.jsonl` | 5 | 1 | 1 | 1 | 同上 |
| `tests/evaluation/golden_set_v2/annotation_workpackage_v1.jsonl` | 70 | 12 | 35 | — | `question` `candidate_contexts[]` `source_ground_truth` |
| `offline/finetune_data.json` | 40 | 12 | 12 | 3 | `output` |
| `tests/evaluation/answer_eval_set.jsonl` | 12 | 2 | 0 | 0 | `question` `expected_behavior` `notes` |
| `tests/integration/testdata/queries.json` | 6 | 0 | 0 | 0 | 无 |
| `docs/demo/synthetic_corpus.json` | 2 文档 | 0 | 0 | 0 | 无（已改用 `DEMO-*` 虚构系列） |
| `tests/evaluation/golden_set_v2/annotations_v1.jsonl` | 0 | 0 | 0 | 0 | 无（空文件） |

黄金集内部分布（按字段出现次数，非互斥）：`contexts[]` 91 条法规名 / 182 条限量、
`ground_truth` 21 / 115、`answer` 14 / 78、`question` 10 / 5。

**关键约束**：`question` 是检索查询，`contexts` 是检索 ground truth。给这两个字段
加行内标识会直接进入检索文本，改变评测难度——这正是任务要求第 6 条禁止的。
因此行内标识只能是**新增的顶层字段**，不能改写任何现有文本字段。

### 1.3 是否存在真实使用者误读的实际路径

**存在一条，且不是夹具本身。** 逐条核查了所有读取方（`api/`、`app.py`、`core/`、
`retrieval/`、`offline/`、`frontend/src/` 对这些文件名的引用数为 **0**）：

| 路径 | 是否会把夹具文本送出仓库 | 判定 |
|---|---|---|
| FastAPI / 前端 / 知识库加载 | 否。`offline/source_discovery.py` 只从运维方指定的 source root 发现语料，从不扫描 `tests/` 或 `offline/` | 安全 |
| Demo（`docs/demo/mock_api.py` → 截图） | 是，但 `synthetic_corpus.json` 已是全虚构标识 + 行内 `_disclaimer`，且 `require_disclosures()` 在缺失标记时**直接中止渲染** | 已处置 |
| 检索基准产物 `artifacts/benchmarks/<run>/per_query_results.jsonl` | **是**。逐行写出夹具 `question` 原文 | **本轮已加标识** |
| 检索基准产物 `metadata.json` | **是**。`corpus.correspondence.missing_examples` 最多带 3 × 120 字黄金段原文 | **本轮已加标识** |
| RAGAS 报告 `data/eval/reports/ragas_report_*.json` | **是**。`per_sample_scores` 带 `question` / `reference_answer` / `retrieved_contexts` | **本轮已加标识** |
| `HUMAN_ANNOTATION_BACKLOG.md`（`audit_annotations.py` 生成） | 是，最多 60 条 `question` 截断文本 | 未处置，见 1.4 |
| QLoRA 训练导出 | 是，训练文本直接由 `instruction/input/output` 模板化 | 未处置，见 1.4 |

也就是说：**sidecar 不随文件走，而 benchmark / RAGAS 的产物才是真正会被转发的那份
文件**。本轮把这三个产物的写出路径补上了机器可读标识（`data_class` /
`carries_real_regulatory_claims` / `notice`），风险因此从"整批产物无标识"降到
"原始夹具行无标识"。

### 1.4 本轮刻意未处置：给原始夹具行加标识（数据集变更）

给 JSONL 每行加 `"_data_class": "SYNTHETIC"` 是最直接的解法，但它是**数据集版本
变更**，不是元数据补充。本轮先做影响分析，是否执行留给下一步决定。已确认的约束：

**解析器/索引器/评测器审计结果——没有任何一个会因为新增顶层字段而失败。**

| 读取方 | 行为 |
|---|---|
| `benchmarks/dataset.py::load_queries` | 只取具名字段，`raw` 原样保留；无多余键拒绝 |
| `benchmarks/golden_set_contract.py` | 只对**缺失/非法必填字段** fail-closed，从不拒绝未知键 |
| `tests/evaluation/ragas_eval.py::format_for_ragas` | 直接下标取 4 个字段，未知键不进 RAGAS 输入 |
| `tests/evaluation/answer_eval.py::AnswerEvalSample.from_dict` | 具名字段进 frozen dataclass，未知键丢弃 |
| `offline/finetune_qlora.py::load_training_data` | 只取 `instruction/input/output` |
| `scripts/validation/build_annotation_workpackage.py` | 只复制具名键 |

**真正会被改动的东西（必须一起做，否则就是回归）**：

1. `golden_set.jsonl` 的 sha256 `36cdaf44…c7df` 被
   `tests/validation/test_golden_set_evidence_docs.py:72` **硬钉**，且同值记录在
   `docs/validation/rag-eval-readiness.md:31,45`（一次已测量的 0/301 契约结果）。
   改字节必须同时更新测试与该文档，否则 CI 失败。
2. `annotation_workpackage_v1.jsonl` 与生成器输出**逐字节相等**
   （`tests/test_annotation_workpackage.py:98`）。标识必须加在生成器里并重新生成，
   否则两者立即漂移。
3. `sample_id` 由**行序号**派生（`benchmarks/dataset.py:183,193`）。新增字段不改变
   行数与顺序，因此 `sample_id` 与 `sample_ids_hash` 不变——这是选择"新增顶层字段"
   而非"插入行"或"重排"的关键理由。多个测试按固定 id 取样
   （`test_review_regressions.py:1158` 的 `["0000","0002"]` 等），重排会直接破坏它们。
4. `benchmarks/corpus.py::_sample_hash` 对**排序后的 `contexts` 文本**取哈希。
   只要不改 `contexts` 内容，`sample_hash` 不变。
5. `tests/evaluation/ragas_eval.py::sample_id_for` 对 `question` 取哈希。改
   `question` 会改 `sample_id`；不改则不变。
6. 行数 301 在 `tests/integration/test_retrieval_benchmark_blocking.py:100` 与
   `tests/validation/test_golden_set_evidence_docs.py:71,77` 被断言。

**因此推荐的最小数据集变更（若决定执行）**：

- 只给 6 个 SYNTHETIC 评测资产加**一个**顶层字段，例如
  `"_data_class": "SYNTHETIC"`，值取自 `DATA_MANIFEST.json` 的 `class`；
- 同时把 `record_marker` 从 `none` 改为 `inline:_data_class`，删除
  `record_marker_absent_reason`；
- `golden_set.jsonl` 按第 1–6 条同步更新 sha、重新测量并重写
  `docs/validation/rag-eval-readiness.md` 的 0/301 段落，并在该文档写明
  "数据集版本已升，旧指标与新指标不可直接比较"；
- `annotation_workpackage_v1.jsonl` 由生成器重新产出并提交；
- 不引入任何未经权威来源核对的法规数值，不改写任何现有文本字段。

**同时要承认这条方案的上限**：行内标识只随**整条 JSON 记录**传播。用 `jq .contexts`
抽字段、或把 `contexts` 数组粘进聊天框的人，带走的仍然是未标注文本。字段级标识
无法解决这个问题；要解决只能改写文本本身，而那会改变被测信号。本轮不制造这种
不可信数据。

### 1.5 本轮未处置的原始理由（保留）

`offline/finetune_data.json`、`tests/evaluation/golden_set*.jsonl`、
`tests/evaluation/sample_golden_set.jsonl`、`tests/integration/testdata/queries.json`
本轮**保持原样**。理由与代价：

- 这四类文件的法规措辞本身就是**被测信号**：检索评测要测的就是"问题里的法规实体
  与语料里的法规条款能否对上"。把实体名换成 `DEMO-*` 会改变检索难度分布，而当前
  评测尚未产出可发布结果；
- 修正数值需要权威来源，反而会把一份"明确标注为虚构"的夹具变成"部分可信"的混合体，
  风险更高；
- 每一条资产现在都在 `DATA_MANIFEST.json` 里显式声明 `record_marker: "none"` 与
  `record_marker_absent_reason`，由 CI 强制。这个缺口从此是**可追踪的已知状态**，
  而不是沉默——这正是它之前的问题所在。

**残余风险（必须承认，不等于已解决）**：sidecar 声明需要读者打开旁边的说明文件。
一份被单独转发、粘贴或检索命中的 `golden_set.jsonl` 行，脱离 sidecar 后仍然读起来
像真实法规结论。1.3 的产物侧标识把最常见的转发路径堵住了，但**原始夹具行的这个
缺口本轮没有关闭**，也没有把它记为已解决。

**待人工完成的核验**：

- [ ] 决定是否执行 1.4 的数据集版本变更（这是工程决定，不需要法规资质）。
- [ ] 处置 `scripts/validation/audit_annotations.py` 生成的
      `HUMAN_ANNOTATION_BACKLOG.md`：该文件会带出最多 60 条 `question` 文本，
      目前无标识（与 1.4 同类问题，但面向的是标注工作流而非评测）。
- [ ] 在引入真实语料前，由具备化妆品法规资质的人逐条审阅下表中的断言，给出"可公开引用
      的公开来源"或"必须删除"的结论。
- [ ] 确认引用源名称的准确写法（例如标准全称、现行版本号、发布机构）。
- [ ] 确认哪些类别（防腐剂、防晒剂、禁限用组分）在目标司法辖区内确实存在限量表，
      避免夹具暗示不存在的机制。
- [ ] 决定 1.2 的四个来源是否接受"sidecar 声明即充分"，还是需要在 JSONL 行内增加
      自描述字段。前者更省事，后者在文件被单独传播时更稳。

### 待核验断言清单

以下断言出现在合成夹具中，均为**虚构占位值**，按主题分组：

| 主题 | 夹具中的断言形态 | 核验要点 |
|---|---|---|
| 铅 / 汞 / 砷 / 镉 / 锑 限量 | 统一给出 mg/kg 数值，并声称适用某统一标准 | 真实限量通常**按品类分别规定**，不存在"适用于所有化妆品类别"的单一数值 |
| 烟酰胺 / 透明质酸钠 / 泛醇 / 神经酰胺 / 积雪草提取物 / 甘草酸二钾 | 给出"最大允许浓度"式百分比 | 需确认这些原料在目标辖区是否**根本不在限用清单内**；若不在，夹具的提问方式本身会诱导模型编造限量 |
| 熊果苷 / α-熊果苷 / 曲酸 / 壬二酸 | 给出"最大允许浓度"式百分比 | 需确认限量是否存在及其适用部位 |
| 视黄醇 | 给出驻留 / 淋洗两档浓度 | 需确认现行限量的实际数值与适用部位 |
| 水杨酸 | 给出驻留类浓度 | 需确认现行限量；部分辖区对儿童化妆品另有限量 |
| 甲基异噻唑啉酮（MIT） | 给出 "0.01%" 类限用数值 | 需确认现行限用与禁用状态；该原料在不同辖区的状态差异较大 |
| 果酸 | 给出百分比区间 | 需确认是法规限量还是 pH 条件下的使用建议 |
| 防腐体系 | 给出复配原则与 pH 活性区间 | 属配方常识而非法规结论，夹具中不应以法规口吻表述 |
| 备案 / 注册 | 给出材料清单、办理周期与编号格式 | 需确认现行制度（备案制 / 注册制的划分、编号规则）与夹具所述一致 |
| 功效宣称 | 引用《化妆品功效宣称评价规范》给出四类证据 | 需确认现行规范中的证据类型表述 |
| 标签与 INCI | 给出含量降序、"1%" 标注阈值等 | 需确认现行标签规则的准确表述 |
| 功效倍数 | "保湿 ×1000 倍"等 | 此类表述**不应**出现在任何监管语境中，核验时建议直接从夹具中删除而非修正 |
| 包装材料 | 引用 GB 4806 系列 | 需确认食品接触材料标准是否适用于化妆品包装；类别适用性存疑 |

> `data/mock_generator.py` 此前是上表中"铅 / 汞 / 砷 限量"一行的另一处来源，
> 已按 1.1 改为 `DEMO-TRACE-*` 虚构示踪物，不再使用真实重金属符号。

---

## 2. 法规语料引入的许可核验

**问题**：本仓库当前**不包含**任何真实法规语料。引入真实语料前必须确认许可。

**待人工完成的核验**：

- [ ] 确认目标辖区内法规文本的**再分发许可**（公开发布 ≠ 允许再分发）。
- [ ] 确认是否需要署名、版本标注或链接回原文。
- [ ] 确认标准组织（ISO、团体标准）的版权条款。
- [ ] 对许可无法确认的材料，按 `offline/source_trust.py` 的 `UNTRUSTED` 级别进入隔离区，
      不进入可激活索引。
- [ ] 记录每份材料的授权依据、许可范围与可公开字段，写入
      [`DATA_MANIFEST.json`](../../DATA_MANIFEST.json)。

---

## 3. Git 历史中的已移除内容

**问题**：本轮从工作树移除的历史生产数字与职业材料仍可从历史 commit 恢复。

**背景**：[`DATA_PROVENANCE.md`](../../DATA_PROVENANCE.md) 第 6 节说明，
本仓库**未**执行 `git filter-repo`、强推或删除分支 / 标签。

**本轮已处置（可逆的公开元数据）**：

- GitHub Issue 与 Pull Request 的**标题与正文**已完成中性化改写，范围为
  Issue #19 / #20 / #22 / #24 / #35 / #47 / #52 / #53 / #58 / #61 与
  PR #15 / #21 / #23 / #25 / #28 / #30 / #33 / #34 / #36 / #48 / #55 / #57 / #67 /
  #68 / #69，另加 Issue #24 / #47 的两条评论。技术背景、问题原因、结果与
  commit / PR / issue 引用全部保留；先前的企业生产规模数字改为引用其
  `HISTORICAL_PRODUCTION` 分类而非复述数值。
- Release `v2.4.0` 的说明与仓库 About 元数据经检查**无需修改**。

**待人工完成的核验**：

- [ ] 确认被移除的内容中是否包含必须吊销的资产（真实凭据、商业秘密）。
      **本轮扫描未发现真实凭据**；工作树移除内容为企业规模数字与职业材料描述。
      这些描述的措辞属于治理用词，**不能**据此断定已发生商业秘密泄露。
- [ ] 若确需历史重写，单独申请授权，并按"先吊销 → 再评估 → 最后授权执行"的顺序进行。
      已确认可从 `main` 历史按 SHA 访问的对象见本节末表。
- [ ] 确认公开标签的处理方案。本轮**未**删除、未迁移任何标签，因为那属于
      不可逆的共享对象破坏操作。

### 当前仍可从 Git 历史访问的对象

以下内容均已从工作树移除，但对象仍可通过 `main` 的历史 commit 访问
（例如 `git show <sha>:<path>`）。路径按普通文本书写而非代码块——这些文件在当前
树里不存在，`check_repo_consistency.py` 会把反引号包住的失效路径判为漂移。

| 已移除内容 | 引入 / 最后修改 | 性质 |
|---|---|---|
| docs/finalization/RESUME_CLAIMS.md | `f3d0305` 引入，`91f70a8` 删除 | 个人职业表述与求职主张的清单。**这是本轮识别出的最敏感对象**：内容是个人对自身经历的陈述与前雇主项目的背景说明，不是源代码。是否需要清除需要单独授权 |
| docs/finalization/INTERVIEW_GUIDE.md | `f3d0305` 引入，`91f70a8` 删除 | 面向技术评审的问题清单 |
| docs/interview-readiness-final-report.md | 历史提交引入，`ecd5af7`（#74）删除 | 项目状态冻结与对外表述记录 |
| docs/interview-architecture-baseline.md、docs/interview-evidence-map.md、docs/interview-walkthrough.md | 历史提交引入；`main` 上前两份已改名为 `docs/architecture-baseline.md` 与 `docs/evidence-map.md`，第三份（走查脚本）已整体移除 | 技术文档，仅旧文件名带面试措辞，内容无个人职业信息 |
| 归档标签 archive/chore-final-interview-readiness-audit-336e325 等 7 个 | 2026-10-04 创建 | 标签**名称**含面试措辞；标签指向的树里只有技术文档，**没有**职业文件 |

标签处置建议（需授权后执行）：删除标签不会从对象库移除提交，但会移除公开可见的
命名。若要彻底移除 `RESUME_CLAIMS.md`，需要 `git filter-repo` 重写并强推，
并同步要求所有协作者重新克隆——**本轮未执行，也不预先授权**。

---

## 4. 审计未覆盖的范围

- [ ] 未访问任何第三方系统的运行日志、数据库或内网，因此**无法**证明合成样例与某家
      真实企业的语料不存在意外重合。
- [ ] 未验证 Git 历史中**已删除文件**的内容合规性（对象仍可通过 SHA 访问）。
- [ ] 未对 `frontend/package-lock.json` 的传递依赖做许可逐一确认。
- [ ] 未核验 §1.2 四个来源中任何一条法规表述的真实数值——这需要资质，且当前
      定位就是虚构夹具。
