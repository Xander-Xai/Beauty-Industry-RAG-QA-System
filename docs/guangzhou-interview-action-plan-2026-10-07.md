# 广州 AI 大模型应用岗位投递执行计划（2026-10-07）

> **文件性质：求职执行清单，不是运行时能力证明。** 本文件只引入外部岗位参考、投入产出判断和一次只处理一个问题的操作提示词。不修改 `docs/interview-architecture-baseline.md`、`docs/interview-evidence-map.md` 的事实权威，不提升任何证据等级。
>
> **审计快照**：2026-10-07，原 `main` HEAD `aa6beb2a2dfc219cf8e7fcf85ae7365bcd35541b`；原仓库 open PR = 0、远程分支只有受保护 `main`、open issue 为 #8/#12/#18/#32/#54/#60。上述数字是**新建本计划 Draft PR 之前的时间点快照**，并非永远不变的 live 指标。CI、Lint、Security 三个 `main` workflow run 均返回 `success`；本文件未重新运行本地完整测试。

## 1. 核心判断

**已经达到“可投递、可面试”的公开仓库门槛；不等于公开仓库证明真实生产规模。** 当前优先级是停止功能堆叠、统一简历和代码口径、能够现场解释实现决策与失败路径。仓库已有：FastAPI canonical 单体；Qdrant + Elasticsearch 动态 2–4 路召回、RRF、BiEncoder + 双 CrossEncoder、双 Gate、RS256 / uint32 RBAC、离线版本构建/封存/人工激活、来源信任隔离、审计、Prometheus/Grafana 规则与可选 OTLP exporter、RAGAS / 检索 / 性能评估框架，以及 Docker Compose 和尚未真实部署的 Kubernetes 静态契约。

必须保留的硬边界：

- `HISTORICAL_PRODUCTION`：上一家公司 3000+ 文档、5000+ 图片、200+ 员工、短时 10–15 QPS、日均 1500+ 请求、双卡 RTX A5000、灰度迁移与奖项。这些不是此仓库的可复现测量；真实面试主张必须由本人可独立举证。
- `REPO_VERIFIED`：代码 + 确定性测试 / CI 证明实现，不等于真实模型效果或生产可用性。
- `PENDING`：未获得真实权重、GPU / 运行环境、独立评估集或部署闭环时，禁止编造 QPS、延迟、RAGAS、真实多模态 smoke、K8s 完整部署和真实浏览器→后端验证。
- 本仓库采用 **Qdrant + Elasticsearch**，不是 Milvus；如历史生产系统使用其他存储，必须用“历史实现 vs 当前公开仓库”明确区分。当前 canonical 是 **FastAPI 单体**，不是已集成的微服务。BLIP 属于在线按需能力，不是固定离线建库步骤。

权威路径：[`README.md`](../README.md)、[`docs/interview-architecture-baseline.md`](interview-architecture-baseline.md)、[`docs/interview-evidence-map.md`](interview-evidence-map.md)、[`docs/interview-readiness-final-report.md`](interview-readiness-final-report.md)。

## 2. 与广州岗位的匹配（公开 JD 样本，非全市场统计）

| 岗位类型及样本 | 岗位重点 | 仓库可展示的证据 | 面试仍需自证 |
| --- | --- | --- | --- |
| **RAG / 企业知识库 / 大模型应用**（荔枝招聘、广州广电国际技术） | 领域数据、清洗/标注、Embedding/Rerank、质量评估、部署 | `offline/`, `retrieval/`, `benchmarks/`, `tests/evaluation/`, `docs/data-admin-guide.md` | 如何构造负样本与 golden set、如何衡量 Recall@K/MRR/nDCG、为何选 Qdrant+ES、如何查一次检索故障 |
| **AI Agent / LLM 后端**（华微软件、字节广州社招职位转载） | Python/接口、RAG、工具调用、工作流、后端联调、Docker | `app.py`, `api/`, `docker-compose.yml`, 这套 RAG 的业务联动 | Agent 的 LangGraph/工具调用应以**另一个真实 Agent 项目**自证，不能说本 RAG 仓库已实现一个生产级 LangGraph 多 Agent |
| **平台与部署偏重**（广州广电国际技术、部分云原生岗位） | GPU 推理服务、量化、并发控制、容器/K8s、性能诊断 | 模型路由代码、`deploy/k8s/` 静态契约、`benchmarks/performance.py` | 真 GPU 部署与 QPS 仍是 `PENDING`；不满足要求“必须现场证明生产集群性能”的职位，应明确降低投递优先级 |

参考 JD（采样日期 2026-10-07；岗位是否仍开放以招聘方平台实时状态为准）：

1. [华微软件：AI 应用工程师（广州，社招）](https://www.huaweisoft.com/ai%E5%BA%94%E7%94%A8%E5%B7%A5%E7%A8%8B%E5%B8%88%E5%B9%BF%E5%B7%9E102026-12-31)：Agent、工具调用、RAG、Dify/扣子、接口与部署、Docker/Nginx。
2. [荔枝招聘：大模型应用工程师（广州）](https://jobs.lizhiinc.com/job/social/detail/7576911295640095018.html)：RAG 优化、企业数据工程、ChatBot、Python/Java、Docker/K8s。
3. [广州广电国际技术：大模型工程师（智联发布）](https://www.zhaopin.com/jobdetail/CC645040330J40842305716.htm)：Embedding / Rerank、Agent、模型服务、安全和可观测。
4. [字节跳动 AI Agent 开发工程师（广州，第三方职位转载）](https://jobs.ultraai.site/jobs/bytedance/7597739886678182197)：Agent / LangGraph / RAG / 多模态 / Python / 服务器端；第三方转载并不代表实时职位已核验开放。
5. [广东盈世计算机科技：2026 AI 开发工程师（广州，校招样本）](https://www.gzrecruit.com/jobs/recruit/detail/CBEF716450D83FFEFD8B677AB4937260)：Agent、工具调用、RAG、上下文与评测；**校招资格未必适合社招候选人，不作为直接投递建议**。

成熟开源方案的借鉴边界：[`infiniflow/ragflow`](https://github.com/infiniflow/ragflow) 强调文档解析、混合检索、引用；[`langgenius/dify`](https://github.com/langgenius/dify) 强调从流程到部署与应用运维；[`langfuse/langfuse`](https://github.com/langfuse/langfuse) 强调 tracing、数据集与回归评估；[`deepset-ai/haystack`](https://github.com/deepset-ai/haystack) 强调模块化可测流水线。**这是设计参考，不是需要全数复制的功能清单。**

## 3. 补齐与不补齐：ROI 决策

时间是实施**估算**，不是仓库测试测得的工时；优先级按“投递 / 面试边际收益 ÷ 时间成本”排序。

| 编号 | 单一任务 | 当前状态 | 预计个人投入 | 不做的代价 | 决策 |
| --- | --- | --- | --- | --- | --- |
| G0 | 主分支、CI、PR/Issue/branch、六级证据回读 | 已核查；无待处理代码 PR | 0–0.5 h 复看 | 容易拿过时状态准备面试 | 已完成，本轮不重做 |
| G1 | 写一页“核心能力→代码→测试→证据等级→边界”速查与 5 分钟演练 | 已有完整索引与 walkthrough | 1–2 h 个人练习 | 现场解释不清，优势无法转化为面试得分 | **P0：先练，不增加新功能** |
| G2 | 简历/投递话术与公开仓库事实一致性 | 需候选人按最新简历实物核查 | 1–2 h | Qdrant/Milvus、单体/微服务、生产历史/仓库指标混淆 | **P0：先做** |
| G3 | 使用合成演示验证“拒答 + 权限切换 + 引用”讲解口径 | 已有 mock demo 和声明 | 0.5–1 h | 面试者误以为 mock 等于真实后端，或演示节奏慢 | **P0：先排练** |
| G4 | 基于真实 bug/异常讲透熔断、降级、安全失败模式 | 代码已存在，需面试理解 | 1–2 h | 容易被追问“你是否只会组装框架” | **P0：先做** |
| G5 | GitHub About、topics、homepage、social preview 完整性 | description/topics/homepage 已与线上匹配；preview 无可核实的 API 回读 | 0.5–1.5 h | 主要影响传播观感，非入职核心能力 | **P1：可选，禁止假 demo** |
| G6 | 再加 GraphRAG/MCP/Agent/多租户/微服务等大功能 | 非本 RAG 岗位当前阻塞项 | 1–5 天或更高 | 不做的机会损失低，做会扩大风险面 | **不做** |
| G7 | 真实模型、负载、集群、RAGAS 与浏览器到真后端的验证 | 现有 6 个 Issue / 12 项 VAL 完整跟踪 | 环境确定后单独估算 | 面试不能声称公开仓库已经验证真实指标 | **延期，本轮绝对不运行** |

**停止开发的判断线**：没有可定位的 P0 错误事实/安全缺陷时，不增新依赖、不新增架构、不拆微服务、不重复创建延期 issue。用于岗位准备的时间优先于继续完善 README。

## 4. 分阶段 DAG：哪些可并行、如何防止冲突

```text
S0 [现况回读：main SHA / PR / issues / branch / CI]       已完成
 |
 +-- S1 [已有 canonical 文档与代码口径核对]              已完成，保留既有文档
 |
 +-- S2 [About + topics + homepage 回读]                已完成，无需改写
 |
 v
S3 [面试准备，可独立并行]
   |-- G1 5 分钟讲解与代码证据定位（只读）
   |-- G2 简历事实核对（不触碰 repo）
   |-- G3 合成 demo 口径与演练（只读）
   |-- G4 面试追问 / 故障剖析（只读）
   \-- G5 社交预览可选设计（不得冒充真实 demo）
 |
 v
S4 [投递并迭代面试笔记]
 |
 +-- LATER [#8 #12 #18 #32 #54 #60，满足环境条件时才验证]
```

**并行规则**：每个执行者只拿一个 G 编号；不同执行者不得修改同一文件；代码/文档变更若确实必要，独立分支、独立 Draft PR、复核后合并；只读任务不得为了制造 diff 随意改文件。任何不在本任务 scope 的问题，只报告而不顺手修复。

## 5. 可复制的单任务操作提示词与验收标准

### G1 — 五分钟代码自证演练（只读）

**提示词**

> 你是 AI RAG 岗技术面试官。只读审查 `docs/interview-walkthrough.md`、`docs/interview-architecture-baseline.md`、`docs/interview-evidence-map.md` 及它们指向的当前 `main` 代码。仅完成一个交付：给我按真实调用顺序的五分钟中文口述稿，每一步标记可打开的文件与函数、确切的证据等级、一个失败边界。不得把 `REPO_VERIFIED` 写成运行期/生产验证；不得扩大成新功能清单；不要提交代码。输出：口述稿、八个尖锐追问、每题不超过 90 秒回答提纲。

**验收**：能解释 `app.py → api/routes.py → core/pipeline.py`、动态 2–4 路选择、RRF、两个重排器、双 Gate、权限二次过滤、Qdrant + ES 依赖；打开路径真实存在，不能称“真实 GPU 性能已经证明”。

### G2 — 简历事实对齐（只读）

**提示词**

> 只完成一件事：将我提供的最新版简历里“化妆品 RAG 项目”每一句可核实技术陈述与 `README.md`、`docs/interview-evidence-map.md`、`docs/interview-readiness-final-report.md`、当前代码逐句对照。输出 `原句｜属于历史生产/本仓库实现/设计目标/尚待验证｜证据路径｜风险｜最小改写句`。优先捕捉 Milvus vs Qdrant、双 GPU vs 单体、历史 QPS vs 本仓库未实测、RAGAS 框架 vs 实际分数、真实模型灰度 vs 配置拓扑。不要为了好看写新指标，不要暗示公司私有数据已公开。

**验收**：每个数字标注出处类型；全部 P0 口径冲突消除；保留真实历史成就但明确“前公司环境”；不修改任何事实权威文档。

### G3 — 合成 demo 的可说明性（只读）

**提示词**

> 只审查 `docs/demo/README.md`、`docs/demo/mock_api.py`、`docs/demo/synthetic_corpus.json`、`frontend/src/App.jsx`、`README.md` 中的演示说明。做一份 90 秒讲解：提问→引用→查看来源→切换身份→越权拒答。必须明确后端用的是 mock API，不声称真实 embedding/vLLM/RAG 推理已在浏览器跑通。不引入新依赖，不截图造性能结果，不改代码。

**验收**：给出演示每步期望现象、真实/合成边界与潜在失败；可对应 `#60` 的待测真实后端 E2E，不把 `#60` 关闭。

### G4 — 工程难题与降级追问（只读）

**提示词**

> 只选一个真实代码路径：`router/vllm_resilience.py` 与使用它的生成路由，或 `retrieval/parallel_recall.py` 与 ES fallback。沿调用路径解释故障入口、超时/重试/降级边界、关键配置、测试覆盖、仍然未验证的运行时事实。输出“问题—排障思路—为什么如此设计—如何写确定性测试—为何不夸大”为骨架的两分钟答案。不得凭空描述生产事故或性能提升。

**验收**：存在可定位的实现和测试、能说出失败时的明确定义、分清测试与真实故障数据，**一次只分析一个路径**。

### G5 — GitHub About / social preview 点检（可选，严格门控）

**提示词**

> 仅读取 live GitHub API 的 description、topics、homepage 和 `docs/repository-metadata.md` 的生效值。逐字符/集合对比；若相同就报告“无需修改”，不要产生空 PR。检查仓库 UI 是否有 social preview；如果无法通过现有权限核验则标成“未核验”，不猜测。需要上传预览时，只使用不含公司敏感数据的合成设计，并由仓库管理员通过 Settings 完成。没有公网 demo 则 homepage 保持空。

**验收**：元数据与 live API 对齐；20 topics 不出现未经验证的能力；任何不可核验的预览都标明未知；无伪造 Demo URL。

### C1 — 如果且仅如果发现一条 P0/P1 文档事实矛盾（条件任务）

**提示词**

> 只处理我指定的**一条**矛盾（给出准确文件路径/行号与代码证据）。先证明它确实存在于最新 `main` 且未被既有 PR 修复；新建 `fix/<single-finding>` 分支，只修改必需的文档行与一个防回归测试（若能机械判定）。不得改其他 README 段落、不得调整架构或证据等级。开 Draft PR，写明 Before/After、验证命令、边界、回滚方式；CI 未绿不申请合并。

**验收**：一个 PR 只消除一个问题；旧表述不再存在；最小改动；一致性 guard 与相关测试成功；所有真实运行指标仍为 `PENDING`。

### C2 — PR / Issue / branch 例行清点（条件任务）

**提示词**

> 先经 GitHub API 只读获取所有 open PR、open issue、远程 branches、默认分支保护状态与最近 CI。对每个 PR 核对 base/head、已合并与否、CI、审核线程；对每个 issue 查找已关闭对应 PR 或尚未满足的验收项；对每个非 main 分支找未合并提交或仍被 PR 引用的证据。只产出 `保留｜关闭｜合并｜删除分支` 的逐项建议，引用 URL。**未经逐项核实不得合并、关闭或删分支；禁止 force push**。若没有操作对象，返回 `NO_ACTION`。

**验收**：数字、编号、状态与 API 一致；6 个延期验证 Issue 均保留；不扰动受保护的 `main`；不擅自将历史实验分支视为已废弃。

### V1 — 真实环境验证（**本轮禁止执行**）

**提示词**

> 只读 `docs/deferred-runtime-validation.md` 和已存在的 #8、#12、#18、#32、#54、#60，汇总 12 个 `VAL-*` 的 `依赖环境｜操作命令｜期望产物｜验收条件｜缺失原因`。本轮不执行任何真实 GPU、RAGAS、集群、压测、模型 smoke，也不写伪造产物；保留所有 `NOT EXECUTED`。不得再创建重复 Issue。

**验收**：12/12 项都有清晰 owner tracker，不缺项、不重复；全部 `PENDING / NOT EXECUTED`；无伪造数字或 PASS。

## 6. 合并 / 冻结边界

- 新发现 **P0 虚假事实、授权绕过或安全缺陷**：可打破功能冻结，但每次只处理一个根因。
- 单纯“高星项目有、我们没有”的功能、漂亮徽章、未验证架构：**不构成打破冻结的理由**。
- 准备投递的最低验收：一次完整 5 分钟讲解、至少 8 个代码级追问可独立回答；简历历史数字与仓库状态零混淆；合成 demo 的 mock 边界能主动解释；没有未审阅、未通过 CI 的功能代码变更。
- 实测环境准备好后，先打开 `docs/deferred-runtime-validation.md`，按对应 issue 一项一项执行，成功产物经过复核后才能升级证据等级。
