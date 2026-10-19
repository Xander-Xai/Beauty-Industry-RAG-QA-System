# DATA_PROVENANCE — 数据来源与使用边界

本文件记录本公开仓库中**每一条随仓库发布的数据资产**的来源、合成方式与使用限制。
它与 [README](README.md) 的证据等级、[数据边界](docs/demo/README.md) 和
[证据地图](docs/evidence-map.md) 一起构成仓库对外的数据契约。

- 仓库身份：面向化妆品知识问答场景的**独立维护的多模态 RAG 工程参考实现**。
- 非关联声明：本项目**不代表任何第三方企业的官方系统或生产服务**，也未获得其背书。
- 边界检查：[`scripts/check_public_repository_boundary.py`](scripts/check_public_repository_boundary.py)
  在 CI 中强制本文件存在且与实际文件一致。

---

## 1. 核心声明

> 本项目为独立维护的化妆品行业多模态 RAG 工程参考实现，不代表任何第三方企业的官方系统或生产服务。
>
> 项目公开的演示数据和测试资料应使用人工构造、合成数据或具有合法公开使用权限的资料，不应包含第三方组织的非公开源码、真实客户数据、内部知识库、产品配方、商业秘密、生产日志、访问凭据或其他未经授权公开的资产。
>
> 示例中的业务记录、产品、原料、文档标识及用户身份仅用于技术演示，不构成真实企业业务记录。
>
> 项目的工程实现与测试通过不代表具备生产环境验收、安全认证或法规合规保证。实际验证范围以可复核证据为准。

上面这段声明是**边界**，不是**检查结果**。下面的第 2–5 节是本次审计实际执行的检查，
两者的范围不同：声明描述"应当如何"，审计记录"实际查了什么、查到什么"。

---

## 2. 数据资产清单

仓库内**全部**随 Git 跟踪的数据文件如下。CI 中的
`check_public_repository_boundary.py --list-datasets` 会按**数据文件扩展名**
（`.json` / `.jsonl` / `.csv` / `.tsv`）重新枚举该清单，而不是按目录白名单，
因此新增到任何目录下的数据文件都必须在此登记才会通过 CI。**条目数不在此写死**
——照抄一个会随迭代变假的整数，正是这份文档要防的那类错误；以上面这条命令的
输出为准，它枚举数据扩展名文件，本表另含非数据扩展名的演示图片。配置文件与依赖清单
（`config.json`、`DATA_MANIFEST.json`、`frontend/package*.json`、Grafana dashboard）
按路径显式排除，理由是它们携带配置而非信息资产。

| 文件 | 行/条数 | 类别 | 来源 | 使用限制 |
|---|---:|---|---|---|
| `docs/demo/synthetic_corpus.json` | 11 keys | `SYNTHETIC` | 人工构造，文件内含 `_disclaimer` 与 `data_class: SYNTHETIC` 自描述字段 | 仅用于生成 README 演示图；不构成任何法规结论 |
| `offline/finetune_data.json` | 40 | `SYNTHETIC` | 人工构造的 QLoRA 训练样例夹具 | **法规文本为虚构占位内容**，见第 4 节；不得用于训练出可对外提供合规结论的模型 |
| `tests/evaluation/golden_set.jsonl` | 301 | `SYNTHETIC` | 人工构造的检索/评测夹具 | 成分限量与标准号均为**虚构占位值**，见第 4 节 |
| `tests/evaluation/golden_set_expanded.jsonl` | 301 | `SYNTHETIC` | 同上（改写扩增变体） | 同上 |
| `tests/evaluation/sample_golden_set.jsonl` | 5 | `SYNTHETIC` | 上述数据集的最小样例子集 | 同上 |
| `tests/evaluation/answer_eval_set.jsonl` | 12 | `SYNTHETIC` | 人工构造的拒答/越权评测用例 | 同上；用例中的"公司内部文件"为**故意的虚构诱饵**，用于验证系统是否会编造引用 |
| `tests/evaluation/golden_set_v2/annotations_v1.jsonl` | 0 | `EMPTY_BY_DESIGN` | 空文件，标注工作尚未执行 | 不得写入合成或模型生成记录；CI 以 `audit_annotations.py --allow-empty` 守卫 |
| `tests/evaluation/golden_set_v2/annotation_workpackage_v1.jsonl` | 70 | `SYNTHETIC` | 由 `scripts/validation/build_annotation_workpackage.py` 从黄金集派生的候选标注工作表 | 是**待人工评审的候选**，不是标注结论 |
| `tests/evaluation/golden_set_v2/annotations.schema.json` | — | `SCHEMA` | JSON Schema 定义 | 不含业务数据 |
| `tests/integration/testdata/queries.json` | 6 | `SYNTHETIC` | 人工构造的集成冒烟查询集 | 含与黄金集相同的**虚构**成分限量问题，见第 4 节 |
| `corpus/real_v1/sources/regulation_727.jsonl` | 81 | `PUBLIC_OFFICIAL_DOCUMENT` | 国务院令第727号《化妆品监督管理条例》派生摘录，来源见 `corpus/real_v1/SOURCES.md` | **真实**官方文本；仅提交派生摘录，不提交原始 HTML；不得篡改原意 |
| `corpus/real_v1/sources/used_ingredients_2021.jsonl` | 128 | `PUBLIC_OFFICIAL_DOCUMENT` | NMPA 2021年第62号公告附件派生摘录，来源见 `corpus/real_v1/SOURCES.md` | **真实**官方文本；仅提交派生摘录，不提交原始 xlsx；INCI 名称来源与残余风险见 SOURCES.md |
| `corpus/real_v1/chunks.jsonl` | 212 | `PUBLIC_OFFICIAL_DOCUMENT` | 由上述两个源文件经 `offline/document_processor` 确定性切块 | 同上；每条 chunk 带 `source_url` 与 `locator` |
| `corpus/real_v1/manifest.json` | — | `PUBLIC_OFFICIAL_DOCUMENT` | 语料版本、指纹、源文件哈希与文档映射 | 不含正文；用于把 `doc_id`/`chunk_id` 绑定到 `corpus_version` |
| `corpus/real_v1/es_fingerprint.json` | — | `RUNTIME_EVIDENCE` | 本机真实 Elasticsearch 索引指纹（仅 BM25 路径） | 仅含指纹与计数，不含语料正文；不声称 dense/hybrid 已验证 |
| `tests/evaluation/golden_set_v1/candidates_v1.jsonl` | 27 | `CANDIDATE` | AI 起草的检索候选，全部 `DRAFT_UNVERIFIED` | **不是** ground truth；未经真实人员审核不得评分 |
| `tests/evaluation/golden_set_v1/no_answer_candidates_v1.jsonl` | 3 | `CANDIDATE` | AI 起草的无答案候选，仅用于 answer-gate | **不是** ground truth；缺席性只相对本语料成立 |
| `tests/evaluation/golden_set_v1/candidate_validation.json` | — | `CANDIDATE` | 候选自动校验结果 | 记录结构审计与语料绑定；不含审核结论 |
| `tests/evaluation/golden_set_v1/retrieval_review_workpackage.jsonl` | 27 | `GOVERNANCE` | 由候选派生的逐条人工审核工作表（含问题派生风险与遗漏段落线索） | **不是**标注结论；decision/reviewer/date 全部留空，`DRAFT_UNVERIFIED` |
| `tests/evaluation/golden_set_v1/answer_gate_review_workpackage.jsonl` | 3 | `GOVERNANCE` | 3 条无答案候选的 Answer Gate 审核表，明确排除在 Retrieval Recall 分母外 | **不是**语义不可回答的结论；关键词缺失只是线索，须人工判断 |
| `tests/evaluation/golden_set_v1/independent_query_proposals.jsonl` | 6 | `GOVERNANCE` | 独立编写的用户式查询提案，`in_dataset=false` | 不进数据集、不可评分、不参与任何指标 |
| `tests/evaluation/golden_set_v1/review_decisions_v1.jsonl` | 27 | `GOVERNANCE` | **空白**人工签署台账，每行绑定其所审核候选行的 `candidate_sha256` 与候选集 `candidate_dataset_sha256` | **不含任何决定**：`decision` / `reviewer` / `reviewed_at` / `promoted_by` 在已提交的行中全部为空。它记录的是"仍待人工决定"，不是决定本身；导入器对带 `ai_*` 字段的行直接拒绝 |
| `tests/evaluation/golden_set_v1/ai_review_v1.jsonl` | 38 | `GOVERNANCE` | **机器可读 AI 预审记录**（`review_type=AI_REVIEW`）：27 条检索候选 + 3 条 Answer Gate + 6 条独立查询 + 2 份来源，逐行给出 verdict、证据、原始与建议金标、已应用变更、未决问题 | **不是人工签署**：每行 `is_human_signature=false`、`promotable_without_human=false`，且不被导入器读取。`AI_ACCEPT` 不等于 `REVIEWED`；来源行的 `AI_SOURCE_VERIFIED_WITH_LIMITATIONS` **不写入** TrustRegistry 审批台账，两份来源仍为 `PENDING_REVIEW` |
| `docs/finalization/REGULATORY_REVIEW_CHECKLIST.json` | 15 条目 | `GOVERNANCE` | 逐条法规断言的人工复核状态追踪器，机器可读 | **不主张任何法规结论**；15 条全部处于 `SYNTHETIC_ONLY`，无一条 `VERIFIED_WITH_SOURCE` |
| `artifacts/qdrant/2026-10-09-qdrant-v1.12.0/checks.json` | — | `RUNTIME_EVIDENCE` | 单台本地主机上对真实 Qdrant 1.12.0 服务的验证检查项 | 仅含 localhost 端点与版本信息；不含语料内容、日志或凭据 |
| `artifacts/qdrant/2026-10-09-qdrant-v1.12.0/metadata.json` | — | `RUNTIME_EVIDENCE` | 同一次验证的来源与兼容性记录 | 同上 |
| `docs/assets/demo-request-evidence-flow.webp` | — | `SYNTHETIC` | 由 `docs/demo/capture_demo.py` 从上述合成语料渲染 | 画面内所有数值均为合成值 |

### 不在清单内的内容

以下内容**不随本仓库发布**，其存在与否由运行环境决定：

- 模型权重（`models/`、CrossEncoder、BGE、CLIP、BLIP、NLI、Qwen）：通过
  `scripts/download_models.sh` 在部署时下载。仓库只记录路径与用途，不分发权重文件。
- Elasticsearch / Qdrant / Redis / MinIO 索引与业务语料：由
  `run_offline.py` 从运维方提供的源文档生成。仓库不携带任何真实知识库。
- 生产日志、监控数据、流量样本、访问凭据：仓库内**不存在**，`.gitignore` 与
  `.dockerignore` 同时排除。

### 生成器不是数据文件，但同样受约束

`data/mock_generator.py` 是**随仓库发布的源码**，不是数据资产，因此不在上表（它是
`.py`，清单按数据扩展名枚举）。但它会生成具有法规文档形状的语料，所以它受第 4 节的
约束与 CI 守卫：

- 它生成的每一个 `.txt` 以合成声明开头，并使用一套**完全虚构**的标识
  （`DEMO-STD-*` / `DEMO-GRP-*` / `DEMO-REQ-*` / `DEMO-ACT-*`、占位 CAS `000-00-0`）。
  它不产生任何真实标准号、真实原料编号或真实限量。
- 输出的 `metadata.jsonl` / `ingredients.jsonl` / `formulas.jsonl` 每条记录都带
  `data_class: "SYNTHETIC"` 与 `carries_real_regulatory_claims: false`。
- 生成目录旁会写出 `DATA_PROVENANCE.md` 说明文件。

`scripts/check_public_repository_boundary.py` 的第 6 项检查在 CI 中强制这一点：
**生成器源码中不得出现真实法规标识**，"真实法规标识 + 限量数值"的组合单独计为
更严重的一类（见第 4 节末段）。

---

## 3. 敏感数据审计结果

审计范围：全部 Git 跟踪文件（本次共 `git ls-files | wc -l` 项）+ 全部公开分支、标签的
可达历史。

| 检查项 | 方法 | 结果 |
|---|---|---|
| 密钥、令牌、证书、数据库凭据 | 模式扫描（AWS / GitHub / Slack / 私钥 / Bearer / 数据库 URI / 高熵串）+ `gitleaks`（若可用） | **未发现**。`.env.example` 全部为占位值（`JWT_SECRET=` 空、`REPLACE_WITH_*`）；`deploy/k8s/secret.example.yaml` 为示例；CI 中的口令为 `ci-only-not-a-secret` 占位串 |
| 生产服务器地址 / 内网地址 | 扫描 IP、RFC1918 网段、内网主机名 | **未发现真实生产地址**。出现的地址均为 `localhost` / `127.0.0.1` / compose 服务名 |
| 真实客户、员工、合作伙伴个人信息 | 扫描姓名、手机号、身份证、邮箱、工号 | **未发现**。审计用身份为 `eval-annotator-a` / `eval-annotator-b` 等角色占位符 |
| 企业内部文档 / 真实配方 / 研发资料 | 逐文件审阅数据资产与文档 | **未发现**。数据资产全部为合成内容，见第 2 节 |
| 供应商、采购价格、生产成本、未公开产品信息 | 关键词与数值扫描 | **未发现** |
| 企业内部业务规则 / 权限配置 / 内部数据结构 | 审阅 `config.json`、`auth/`、`common/auth.py`、`deploy/k8s/` | **未发现**。权限配置是本项目自身的 `uint32` 掩码设计，不是任何第三方的权限模型 |
| 未经授权公开的模型权重 / 数据集 / 训练材料 | `git ls-files` 扩展名扫描 | **未发现**。仓库无 `.onnx` / `.safetensors` / `.pt` / `.pth` / `.bin` / `.pdf`；唯一二进制资产是合成渲染的 `.webp` |
| 可交叉关联重新识别个人或企业的内容 | 组合搜索（规模数字 × 硬件型号 × 时间线 × 组织名） | 本轮已清除。原 `docs/final-canonical-runtime-audit.md` 曾逐项列举十一个历史生产数字，`docs/slo-runbook.md` / `docs/evidence-map.md` / `CHANGELOG.md` / `app.py` / 四个 finalization 文档曾标注具体生产硬件型号，均已移除 |
| 未经授权复制的企业代码、设计图或知识产权 | 代码与文档审阅 | **未发现**。全部代码为本仓库自有实现 |
| 前雇主 / 职业经历叙述 | 关键词扫描（离职、前雇主、任职、企业项目代号、求职类词汇） | 本轮已清除。`docs/demo/README.md` 的"上一家公司语料"、`docs/README.md` 的 "former employer" 表述已中性化 |

### 未覆盖的范围（不构成保证）

- 本次审计**没有**验证 Git 历史中已被删除文件的内容合规性。历史对象仍可通过
  commit SHA 或旧标签访问，处置方案见 [历史可访问性](#4-历史可访问性)。
- 本次审计**没有**访问任何第三方系统的运行日志、数据库或内网，因此**无法**证明
  本仓库中的样例数据与某家真实企业的语料不存在意外重合。
- 合成数据中的法规内容**未被逐条对照官方标准校验**（见下一节）。

---

## 4. 化妆品法规内容的特别声明（重要）

**本仓库数据集中的法规、标准号、成分限量与安全结论均为人工构造的占位内容，
不是真实法规知识，也未经任何监管机构或第三方标准原文校对。**

> **范围说明（自 Golden Set V1 起）：** 上面这句描述的是**合成夹具**。仓库现在另有一份
> **真实公开语料** `corpus/real_v1/`，其文本来自官方文档并带来源与许可证据，见第 4.1 节。
> 两者不得混用：合成夹具用于形状测试，真实语料用于检索评测；任何法规结论仍须回到官方现行文本核对。

具体而言，下列内容出现在 `offline/finetune_data.json`、
`tests/evaluation/golden_set*.jsonl`、`tests/evaluation/sample_golden_set.jsonl` 与
`tests/integration/testdata/queries.json` 中，其形式模仿真实法规表述，但数值是虚构的：

- 以真实法规名称（如《化妆品安全技术规范》《化妆品监督管理条例》《化妆品标签管理办法》）
  为引用来源，配以虚构的成分最大使用浓度；
- 对不设限量的常见功效性原料（烟酰胺、透明质酸钠、泛醇、神经酰胺、积雪草提取物等）
  给出"最大允许浓度"式数值；
- 以倍数表述的虚构功效数据（如"保湿 ×1000 倍"）；
- 版本号、标准号、备案号格式等占位串。

这四个来源是**已知的、声明齐备但仍保留混合形态**的资产：它们由检索评测与训练夹具的
形状要求决定，改成虚构标识会降低夹具的检索难度分布，且当前评测尚未产出可发布结果。
处置理由与代价见
[`docs/finalization/DATA_REVIEW_BACKLOG.md`](docs/finalization/DATA_REVIEW_BACKLOG.md) 第 1 节。

相比之下，**不在上述清单中的合成来源已经改掉了这个形态**：`data/mock_generator.py`
过去生成 `GB/T` / `QB/T` / `NMPA` / `HFJB` 标题、真实 INCI 与真实 CAS 编号，并附带
虚构的重金属限量，写成真实技术标准的样子。现在它使用与
`docs/demo/synthetic_corpus.json` 同一套虚构标识，并在每个输出文件里独立声明。

**这些内容存在的唯一目的，是让检索、重排、Evidence Gate、Answer Gate 与评测
harness 有一条真实形状的输入可跑。** 它们不是安全评估依据，不得用于配方开发、
合规判定、成分限用判断或任何面向消费者的答复。

因此：

1. 任何基于本仓库数据集产出的答案，都必须在被替换为**经授权的公开法规原文语料**之后
   才具备监管意义。数据契约（字段、分发版本、标注来源、评审状态）由
   `benchmarks/golden_set_contract.py` 与 `benchmarks/provenance.py` 定义，替换语料时
   契约不变。
2. `tests/evaluation/answer_eval_set.jsonl` 中 `AE-001` 一类的用例，其
   `expected_behavior` 是"给出数值并引用规范文件"。该用例描述的是**评测意图**
   （当语料中存在显式限值时，系统应当给出并引用它），**不是**对某个具体数值正确性的
   背书。语料替换后，该用例必须重新评审。
3. 未替换语料前，本仓库的定位是**检索工程与答案门控的实现验证**，不是化妆品法规知识库。

### 混合形态的处置原则

"真实法规名称 + 虚构限量值"是本仓库里唯一一种**单独声明不足以化解**的风险：
读者看到《化妆品安全技术规范》这个名称时，会把它当作可信度来源，而不会去查
旁边那行"本数据为虚构"。因此处置顺序是固定的：

1. **优先改标识，而不是改数值。** 把引用源换成仓库自造的虚构标准系列
   （`DEMO-STD-*`、`DEMO-ACT-*`、占位 CAS `000-00-0`），数值保持虚构。
   `data/mock_generator.py` 与 `docs/demo/synthetic_corpus.json` 已经是这个形态。
2. **其次加强标注与边界隔离。** 每个生成文件独立携带声明，输出目录旁写出
   `DATA_PROVENANCE.md`，每条记录带 `carries_real_regulatory_claims: false`。
3. **只有在有可靠来源、合法使用依据和对应回归测试时**，才允许引入真实法规数据。
   三者缺一，就维持虚构标识。

### 机器可读的披露状态（`DATA_MANIFEST.json`）

声明不能只存在于散文里。每个资产在清单中都必须写明三件事：

| 字段 | 含义 |
|---|---|
| `carries_real_regulatory_claims` | 该资产是否声称携带真实法规结论。当前全部为 `false`；翻成 `true` 需要来源 + 许可 + 回归测试，是一次可评审的变更而不是安静的一次编辑 |
| `record_marker` | **一条记录脱离本文件后如何自证身份**。`inline:<field>` 表示该字段随每条记录传播；`file` 表示文件顶部一次声明覆盖全文件；`none` 表示记录不带自描述 |
| `regulatory_shaped_fields` | 哪些键的值引用了真实法规标识或限量数值。构造脱敏视图、外部摘录或训练导出时，恰好应当把这几个键当作合成内容处理 |
| `record_marker_absent_reason` | `record_marker: "none"` 时必填。**"没有修"必须是一句可追踪的理由，而不是一处省略** |

`scripts/check_public_repository_boundary.py` 在 CI 中强制以上四项。

### 产物侧的标识（比夹具本身更关键）

sidecar 不随转发走，因此**携带数据集文本的产物**必须自带标识。
`benchmarks/provenance.py::dataset_data_provenance(path)` 是唯一来源，返回
`data_class` / `carries_real_regulatory_claims` / `notice` 三项，当前写入：

- `artifacts/benchmarks/<run-id>/metadata.json`
- `artifacts/benchmarks/<run-id>/per_query_results.jsonl`（**每行**）
- `artifacts/benchmarks/<run-id>/report.md`（正文第一段，低于标题）
- `data/eval/reports/ragas_report_*.json`（顶层）与对应的 Markdown（表头之前）
- `tests/evaluation/golden_set_v2/HUMAN_ANNOTATION_BACKLOG.md`（由
  `scripts/validation/audit_annotations.py` 生成，标题之后、问题表格之前）
- QLoRA 训练输出目录的 `DATA_PROVENANCE.json` 与模型卡 `README.md`（由
  `offline/training_provenance.py` 生成，见 `offline/finetune_qlora.py`）

这些产物此前会把夹具的 `question`、`contexts`、参考答案原样写出且不带任何标识，
是实际最可能被复制传播的路径。

**标识必须由数据集本身推导，不能写死。** `--dataset` 与 RAGAS 的 `dataset_path` 接受
任意 JSONL，包括运维方自有语料或经授权的真实语料。写死 `SYNTHETIC` 会在那一刻变成
一句假话，而"把真实数据标成合成"比什么都不标更危险。因此
`dataset_data_provenance()` 从 [`DATA_MANIFEST.json`](DATA_MANIFEST.json) 查出该路径
声明的类别：

| 查询结果 | 产物写入 |
|---|---|
| 清单声明为 `SYNTHETIC` 且 `carries_real_regulatory_claims: false` | `data_class: SYNTHETIC` + 完整声明 |
| 清单声明为其他类别（如 `RUNTIME_EVIDENCE`） | 该类别 + 清单里的声明状态 |
| **不在清单中**（运维方语料、经授权语料、仓库外路径） | `data_class: UNCLASSIFIED`，`carries_real_regulatory_claims: null`，**不写声明** |

最后一行是关键：无从核实的路径拿不到任何断言，包括那句听起来让人放心的
"这不是真实法规数据"。RAGAS 报告的 `data_class` 默认值同样是 `UNCLASSIFIED`
而非 `SYNTHETIC`，因此一个在本变更之前写出的旧报告被读入时，不会被默认值
追认为"合成且无真实法规主张"。

标注任务单是这一族里唯一的例外：它对 `UNCLASSIFIED` 会写出
`UNCLASSIFIED_DATA_NOTICE`，因为该文件的主要读者是人，而**沉默**才是那里真正
的危险形态——一份没有任何来源声明的工作单会被当成已核验材料使用。该声明只陈述
"来源未登记、性质未确认"，既不断言其为合成，也不断言其为真实法规资料，因此不与
上表冲突。回归用例见 `tests/validation/test_annotation_backlog_disclosure.py`。

**已经关闭的缺口**：`HUMAN_ANNOTATION_BACKLOG.md` 此前最多带出 60 条 `question`
文本却没有任何标识。它由 `audit_annotations.py` 生成时会按 `--source-dataset`
解析类别，并把 `data class` / `carries real regulatory claims` / 源文件路径与
SHA-256 / 合成声明写在标题之后、问题表格之前——单独复制这份 Markdown 的读者
不需要打开 sidecar 就能知道它是什么。清单里查不到的源数据集得到
`UNCLASSIFIED` 与"来源未经确认"的说明，同样不会得到任何肯定性断言。

**仍然存在的缺口**：原始 JSONL 行本身仍没有行内标识。这是刻意推迟的数据集版本
变更，理由与代价见 backlog 第 1.4 节。

CI 中强制第 1、2 条的是 `check_public_repository_boundary.py` 的第 6 项检查
（`fabricated_regulatory_authority_violations`）与
`tests/test_mock_generator.py`；回归用例在
`tests/test_check_public_repository_boundary.py`。第 3 条无法自动化，只能由
具备资质的人判断，清单见
[`docs/finalization/DATA_REVIEW_BACKLOG.md`](docs/finalization/DATA_REVIEW_BACKLOG.md)。
需要替换为真实语料时，按 [`docs/benchmark-data-quality.md`](docs/benchmark-data-quality.md) 的数据准入流程执行，来源许可无法确认的材料先进入隔离区
（`offline/source_trust.py` 的 `UNTRUSTED` 信任级别），经人工批准后才可激活。

### 4.1 真实公开语料（`corpus/real_v1/` V1）

自 Issue #86 / #18 的推进起，仓库包含**第一份真实公开语料**，用于把检索评测从
"规范化精确文本匹配"提升到真实 `(doc_id, chunk_id)` 归因：

| 项 | 内容 |
|---|---|
| `corpus_version` | `real-v1-20261019` |
| 数据类别 | `PUBLIC_OFFICIAL_DOCUMENT`（在 `DATA_MANIFEST.json` 中声明） |
| 来源 1 | 《化妆品监督管理条例》国务院令第727号，www.gov.cn 官方全文 |
| 来源 2 | 《已使用化妆品原料目录（2021年版）》NMPA 2021年第62号公告附件 |
| 许可依据 | 《中华人民共和国著作权法》第五条第（一）项：国家机关的决议、决定、命令和其他具有立法、行政、司法性质的文件不适用著作权法 |
| 再分发 | **允许**（官方文件）。仓库只提交**派生摘录**与来源/许可说明，不提交原始 `.xlsx` 或官方 HTML |
| 可信性 | 最高：两份文件均为发布机构的官方文本 |
| 指纹 | 本地指纹 `manifest.json::corpus_sha256_local`；ES 索引指纹 `es_fingerprint.json::fingerprint.content_sha256` |

**边界与限制**

1. 本语料是 **2021 年版快照**，不声称是最新版本；语料更新必须生成新的
   `corpus_version` 与指纹，不能原地覆盖。**监管版本时效性核查（2026-10-19）见
   `corpus/real_v1/SOURCES.md` 的"监管版本时效性核查"一节**：条例第七十八条过渡期已于
   2025-12-31 届满（配套 2021年第150号公告不在本语料内）；《已使用化妆品原料目录》已于
   2025年第61号公告转为《目录》Ⅰ/Ⅱ 动态管理并**删除"最高历史使用量"项目**，至
   2026-08-19 已历经五次动态调整。
2. 目录中的"最高历史使用量"在说明第八条中被定义为安全评估证据类型之一，
   **不是**强制法规限量；任何把它当作限量的回答都是对原意的篡改。**该字段亦已被
   2025年第61号公告从现行目录中删除**，只可用于历史版本检索实验。
3. 本语料**只收录 127 行**精选记录，官方目录共收录 8965 种原料。**语料中查不到某原料，
   不代表该原料不在现行目录内**，不得以本语料回答"某原料是否已使用"。
4. INCI 名称的原始词典来源是 PCPC；本仓库只摘取事实性名称/数值字段，不复制
   词典解释文本。残余风险已在 `corpus/real_v1/SOURCES.md` 如实记录。
5. `tests/evaluation/golden_set_v1/` 下的候选标注**全部**为 AI 起草的
   `DRAFT_UNVERIFIED`，`scorable: false`。只有具名人员审核并按
   `benchmarks/annotation.py` 提升为 `REVIEWED` 后，才允许进入评测。
6. 真实语料的存在**不**降低对合成夹具的披露要求；`DATA_MANIFEST.json` 中两份
   数据的类别、`carries_real_regulatory_claims` 与 `regulatory_shaped_fields`
   分别声明，不得互相覆盖。



---

## 5. 变更本文件时必须同步的事情

1. **新增数据文件** → 在第 2 节登记，并确定 `SYNTHETIC` / `RUNTIME_EVIDENCE` /
   `EMPTY_BY_DESIGN` 类别。CI 会拒绝未登记的数据文件。
2. **引入真实语料** → 在第 4 节改写声明，记录授权依据、许可范围与可公开字段，
   并把受许可限制的原始内容留在仓库之外，只提交脱敏后的标识与契约。
3. **修改第 3 节结论** → 同步更新 `scripts/check_public_repository_boundary.py`
   中的断言与 `tests/test_check_public_repository_boundary.py`，两者必须一起改。
4. **新增一个会生成法规形状语料的源码** → 它是 `.py`，不进第 2 节清单，但要满足
   第 2 节末"生成器不是数据文件"一节的披露要求，并会被第 6 项检查
   （`fabricated_regulatory_authority_violations`）覆盖。

---

## 6. 历史可访问性

第 3 节的结论针对**当前代码树**。Git 历史是独立的风险面：

- 已从工作树移除的内容仍可从历史 commit 恢复。本仓库**没有**执行
  `git filter-repo`、强推或删除分支/标签，因为这些是不可逆的共享历史破坏操作，
  需要单独授权。
- 已公开过的对象无法通过后续提交"收回"。若某个具体资产被判定为必须吊销
  （真实凭据、商业秘密），正确处置是：**先吊销凭据 / 停止使用 → 再评估历史清理成本 →
  在获得明确授权后执行历史重写**。本文件不预先授权任何破坏性操作。
- 人工核验清单见 [`docs/finalization/DATA_REVIEW_BACKLOG.md`](docs/finalization/DATA_REVIEW_BACKLOG.md)，
  其中列出许可来源无法确认、需要人工判断的材料。

---

## 7. 联系方式与纠错

本文件描述的是**当前公开状态**。如果你认为某条内容不应公开，请通过
[Security Policy](SECURITY.md) 的私密渠道联系，而不是公开开 issue —— 公开 issue
本身就会复制被质疑的内容。
