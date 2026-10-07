# 5 分钟技术 Walkthrough（screen-share 脚本）

按技术评审会问的顺序，逐节给出**要说的话**与**要打开的真实代码文件**。目标：5 分钟内让 reviewer 知道具体做了什么，而不是让他读完 26,000+ 字 README。

本文档**不建立任何新的事实来源**。架构口径唯一来自 [architecture-baseline.md](architecture-baseline.md)，证据等级唯一来自 [evidence-map.md](evidence-map.md)，逐能力审计唯一来自 [repository-truth-audit.md](repository-truth-audit.md)。本文只做**导航**与**话术**：每一节指向的那几个文件，才是答案本身。

## 怎么用这份脚本

| 用法 | 做法 |
|---|---|
| 提前准备 | 通读一遍，把每节的「打开这些文件」在本地先打开一次，避免现场找文件 |
| 评审进行 | 从「业务背景」开始按节跳。**对方打断就停下来回答**，不要念稿 |
| 时间不够 | 只讲「整体架构」→「一次 Query 如何经过系统」→「Hybrid Retrieval」→「双 Gate」→「当前未验证边界」五节，其余按对方追问补 |
| 想展示严谨 | 主动讲最后一节。**主动交代未验证边界，比被问到再答可信得多** |

每节固定三段：

1. **说**——可以直接念的口语稿。
2. **打开这些文件**——真实路径，点开对应行号。
3. **边界**——这一节哪些是 `REPO_VERIFIED`、哪些是 `PENDING`。等级词汇只使用上面三份文档里的那套，不引入第二种说法。

**证据等级词汇**（完整定义见 [Classification vocabulary](evidence-map.md#classification-vocabulary)）：`HISTORICAL_PRODUCTION` / `HISTORICAL` / `REPO_VERIFIED` / `LOCAL_REAL_VALIDATION` / `DESIGN_TARGET` / `PENDING`。`EXECUTED` / `PARTIAL` / `BLOCKED` / `PASS` / `NOT RUN` 是单次运行结果，不是证据等级。

---

## 业务背景

**说**

> 化妆品行业的法规、成分和产品资料分散在 PDF、图片、Excel 和内部系统里。人工查一条法规要翻多个文件，通用大模型能给出通顺答案但会编造条款编号和限量值，而不同角色该看到的内容也不一样——法规事务和商务团队看到的答案不该相同。
>
> 所以这个系统的目标不是"接一个 RAG"，而是三条硬需求：**答案必须带引用并能点回原文；证据不足时必须拒答；同一个人在切换角色后必须看不到越权文档**。拒答和权限隔离是业务需求，不是加分项。

**打开这些文件**

- `docs/architecture-baseline.md` → 「标准架构说明」（先看这一段，最省时间）
- `PRD.md` → 顶部 runtime reconciliation 表
- `frontend/src/App.jsx` → 演示里"点引用跳回来源文档"的交互

**边界**：业务背景里的规模数字属于 `HISTORICAL_PRODUCTION`（前雇主生产环境），**不是**本仓库 benchmark。详见 README 的 [Historical Production Context](../README.md#historical-production-context)。

---

## 整体架构

**说**

> 两条链路分开的。离线侧把 PDF、图片、表格解析成统一的知识资产写进 Qdrant 和 Elasticsearch；在线侧是一个 **FastAPI 单体主链路**，不是已完成联调的微服务。
>
> 在线顺序是：身份解析 → 带知识版本和权限指纹的缓存 → Query Rewrite 与复杂度判断并行 → **动态 2 至 4 路召回** → 权限二次过滤 + 加权 RRF → 两级重排 → Evidence Gate → 模型路由生成 → Answer Gate。
>
> 检索不是固定四路：简单问题 2 路，复杂问题 3 路，复杂且视觉相关才 4 路。ES Fallback 是降级补召回，不算第五路。

**打开这些文件**

- `app.py` → 单体入口，proxy 策略
- `api/routes.py` → `/api/query` `/api/chat` `/api/continuation`
- `core/pipeline.py` → `OnlineRAGPipeline.process()`，整条链路在一个方法里顺序展开
- `docs/architecture-baseline.md` → 「当前主链路」代码块

**边界**：**架构口径只认上面那份 baseline**。`api-gateway/`、`retrieval-service/`、`generation-service/`、`monitoring-service/` 目录是保留的代码组件，不代表端到端生产验证。

---

## 一次 Query 如何经过系统

**说**（这段按 `core/pipeline.py` 的行号往下念，效果最好）

> 一次提问进来，先做身份解析拿到 `role_mask` / `dept_mask`，再用「知识版本 + 权限指纹」拼缓存键。缓存未命中时，**Query Rewrite 和复杂度判断并行跑**。
>
> 复杂度判断决定这次问几路召回：简单 2 路，复杂 3 路，复杂且视觉相关 4 路。多路结果先过文档级权限二次过滤，再加权 RRF 融合。
>
> 融合结果进两级重排：BiEncoder 宽保留 150，双 CrossEncoder 精排到 10。然后 Evidence Gate 决定正常生成 / 增强证据生成 / 拒答；生成完 Answer Gate 再校验答案和证据是否一致。

**打开这些文件**

- `core/pipeline.py` → `process()`（约 207 行起）→ `_build_cache_key()` → `_merge_and_dedup()` → `_build_rrf_weights()`
- `core/pipeline_context.py` → `PipelineContext`，所有降级状态挂在 `degraded` / `fallback_reason` 上
- `tests/test_pipeline_ordering.py` → 阶段顺序由契约测试锁死

**边界**：链路顺序是 `REPO_VERIFIED`。链路上每一跳的**运行时**结果（真实延迟、真实召回效果）是 `PENDING`。

---

## Hybrid Retrieval

**说**

> 四条可选路径：`dense_bge`（Qdrant 文本集合）、`bm25_es`（Elasticsearch）、`rewrite_variant`（Rewrite 变体再走 BGE）、`clip_visual`（CLIP 图像集合）。是否启用后两条由复杂度判断和视觉判别器决定，不是固定配置。
>
> 融合用的是**加权 RRF，权重随问题类型变**：法规类问题提高 BM25 权重，视觉相关问题提高 CLIP 权重。这不是把所有路径等权相加。

**打开这些文件**

- `retrieval/parallel_recall.py` → `ParallelRecallManager.execute()`；`_apply_rbac_filter()`；`_recall_es_fallback()`；`_compute_agreement_score()`
- `retrieval/dense_retriever.py`、`retrieval/bm25_retriever.py`、`retrieval/clip_retriever.py` → 三条路径各自的适配器
- `models/complexity_evaluator.py` → 复杂度判定的实际实现
- `core/pipeline.py` → `_build_rrf_weights()` → 业务类型到路径权重的映射
- `tests/test_rrf_fusion.py`、`tests/test_parallel_recall.py` → 融合逻辑的确定性测试

**边界**：路由与融合逻辑是 `REPO_VERIFIED`。检索**质量**指标（Recall / NDCG / MRR）是 `PENDING`：框架在 `benchmarks/`，仓库里没有任何可复现 artifact，所以本仓库文档不给任何检索数字。

---

## Rerank

**说**

> 两级。第一级 BiEncoder 便宜，把 150~200 条候选宽保留到 150 条；第二级是**两个 CrossEncoder 的 ensemble**，精排到 10 条，两个模型的分数经 Platt 校准后合成 ensemble 分。
>
> 一个容易被追问的点：仓库里还有一个跨请求异步聚合批量预测的接口，但它**没有**接进当前主调用，主链路用的是请求内批量预测。不应说"已经做了跨请求动态微批"。

**打开这些文件**

- `retrieval/bi_encoder.py` → `BiEncoderReranker.rerank()`，`top_k=150`
- `retrieval/cross_encoder_ensemble.py` → `CrossEncoderEnsemble.rerank()`、`PlattScaler`
- `retrieval/rerank_batch_aggregator.py` → 异步聚合接口（**注意它未接入主调用**）
- `tests/test_bi_encoder_rerank.py` → 第一级重排的确定性测试

**边界**：两级重排的实现是 `REPO_VERIFIED`。真实模型权重下的重排质量与 GPU 批处理吞吐是 `PENDING`（真实权重不在仓库）。

---

## Evidence / Answer Gate

**说**

> 这是整个项目最像"工程"而不是"demo"的地方——**两层门控把幻觉挡在生成前后**。
>
> 生成前的 Evidence Gate 综合四个信号：CrossEncoder Top 1 分、Top 3 平均分、多路召回一致性、Top 3 文档之间的一致性。输出三选一：正常生成、增强证据后生成、拒答。
>
> 生成后的 Answer Gate 再校验答案是否忠于核心证据，法规类矛盾直接拒答。**拒答是设计内的正常输出**，不是异常。

**打开这些文件**

- `retrieval/evidence_gate.py` → `EvidenceEnsembleGate.evaluate()`（四个信号在这里合成；`conservative_mode` 在 Rewrite 降级时自动抬高阈值）
- `retrieval/answer_gate.py` → `AnswerGate`
- `core/pipeline_context.py` → `EvidenceGateResult`，`decision` 字段的取值
- `tests/test_architecture_contract.py` → 两层 Gate 的存在性与契约由测试锁定

**边界**：两层的判定逻辑是 `REPO_VERIFIED`。**阈值标定**是 `PENDING`——阈值来自 `config.json` 的设计值，没有用真实标注数据调过，所以不能说"拒答率已经优化到某个水平"。

---

## 多模态 Ingestion

**说**

> 离线侧支持 UTF-8 TXT、PDF（区分文本页与扫描页，扫描页走 OCR 路由）、DOCX、XLSX，以及独立图片（PNG/JPEG/WebP/BMP/TIFF）。切块默认 500 字符 / 10% 重叠。
>
> 知识版本有**发布纪律**：构建、校验、封存可以自动化，但把 `knowledge_version_epoch` 切到新版本是**显式人工步骤**。封存之后 epoch 不可变。调度器永远不会自动激活 epoch——DAG 代码存在不等于调度器在跑。

**打开这些文件**

- `offline/document_processor.py` → `DocumentProcessor.process()`，`_process_pdf()`（文本页 / OCR 路由分流）
- `offline/image_processor.py` → `OCRProvider` 协议、OCR 结果结构
- `offline/embeddings.py` → BGE 文本与 CLIP 图像共用的池化与归一化契约
- `offline/chunking.py`、`offline/text_ingestion.py`、`offline/qdrant_writer.py`、`offline/elasticsearch_writer.py`
- `offline/scheduler.py` → 增量 / 全量周期；`offline/snapshot_builder.py`、`offline/validation.py` → 快照校验与封存
- `run_offline.py` → CLI；`dags/knowledge_base_dags.py` → Airflow DAG（**注册代码，不代表在运行**）
- `docs/data-admin-guide.md` → 激活 epoch 的操作步骤

**边界**：整条离线管线是 `REPO_VERIFIED`。真实 BGE / CLIP / PaddleOCR 的模型权重不在仓库，真实模型 smoke 是 `PENDING`（`EXTERNAL_MODEL_ASSET_REQUIRED`）；真实 Airflow DAG 执行也是 `PENDING`。

---

## RBAC

**说**

> 权限是 uint32 位掩码，role 和 dept 各一张掩码。控制分三层，是纵深防御：
>
> 第一层**存储下推**——Qdrant 下推文档状态（启用知识版本时同时下推版本），Elasticsearch 下推状态、版本、role、dept。第二层**融合前二次过滤**——所有召回适配器保留 role/dept 元数据，融合前统一过一遍位掩码谓词，ES Fallback 复用正常 BM25 的权限查询，不能绕过。第三层**缓存物理分区**——L2 key 里带权限指纹，旧的无分区 key 不回落，宁可 miss。
>
> 认证以 RS256 为准。畸形授权声明 **fail closed**，不做静默回退。登录限流 5 次/分钟，只有当 TCP 对端在 `TRUSTED_PROXIES` 里才解析 `X-Forwarded-For`，否则客户端伪造 XFF 绕不过去。

**打开这些文件**

- `auth/bitmask_rbac.py` → `is_allowed()`、`build_qdrant_filter()`、`build_qdrant_image_filter()`
- `common/auth.py` → `_validate_permission_mask_claim()`（fail-closed 在这里）、`encode_role_mask()`
- `retrieval/parallel_recall.py` → `_apply_rbac_filter()`（融合前二次过滤）
- `retrieval/bm25_retriever.py` → ES 侧的权限查询下推
- `auth/jwt_auth.py` → RS256 签发与校验
- `api/routes_auth.py` → 登录、刷新、用户与角色管理
- `tests/test_bitmask_rbac.py`、`tests/test_retrieval_authorization_contract.py`
- `docs/security-regression-coverage.md` → 固定威胁清单与**仍然开放的有界缺口**

**边界**：位掩码语义、下推、过滤与 fail-closed 都是 `REPO_VERIFIED`。生产环境的权限策略审计是部署侧的事，本仓库不断言。检索证据的 prompt 层结构约束（`<retrieved_context>` / `<user_query>` 保留标记转义）是**纵深防御的一层，不是强隔离**——它不解决 prompt injection，也不代表越狱已被证明不可能。

---

## Cache

**说**

> 两级。L1 是进程内缓存，**只有公开数据才进 L1**；其他权限组合走 Redis L2。
>
> 缓存键包含知识版本和权限指纹，所以换知识版本或换身份不会命中旧结果。降级路径是明确的：Redis 挂了退到进程内内存，而不是报错。

**打开这些文件**

- `cache/redis_cache.py` → `RedisCache`，L2 key 分区由 cache 对象自身强制
- `core/pipeline.py` → `_build_cache_key()`、`_scope_cache_key_to_session()`
- `tests/test_cache.py`

**边界**：键构造与分区是 `REPO_VERIFIED`。真实 Redis 多进程会话持久化与跨进程登录限流是 `LOCAL_REAL_VALIDATION`（[validation/v2.5-runtime-security-validation.md](validation/v2.5-runtime-security-validation.md)）。**Redis Cluster / Sentinel 生产拓扑未在本仓库验证**。

---

## 模型路由

**说**

> 当前生成拓扑是**一个共享的 4B vLLM 端点**，Query Rewrite 和简单生成都用它，复杂请求路由到 Qwen3-14B。路由本身无状态，按业务类型和复杂度决定档位；KV 压力过高时可以截断、降级或拒绝。
>
> 旧 PRD 里的"独立 vLLM-Rewrite + vLLM-Gen-4B 双实例"是历史 / 目标设计，不是当前实现——这里主动说清这一点。

**打开这些文件**

- `router/stateless_router.py` → `StatelessRouter`
- `models/llm_client.py` → `LLMClient`，以及 `<retrieved_context>` / `<user_query>` 保留标记常量与转义
- `models/adapter_manager.py` → 适配器生命周期
- `config.json` → `gpu1.models.vllm_4b`（共享 4B）与 `gpu0.models.gen_14b`
- `tests/test_architecture_contract.py` → 路由契约

**边界**：**路由契约**是 `REPO_VERIFIED`；**真实 4B / 14B vLLM GPU 部署**是 `PENDING`——权重不在仓库，`vllm` 未安装，"双模型已完成生产压测"不是本仓库的事实。

---

## Observability

**说**

> 可观测端点是 `/api/metrics`，**需要认证**（Prometheus 抓取要带 Bearer token）；`/api/health` 是公开的诊断端点，`/api/ready` 是给流量准入用的、不满足能力返回 503。
>
> 运维资产我只列真实存在的：6 条 Prometheus 告警规则，**每条都只引用应用真实 emit 的 series**；10 个面板的 Grafana JSON；9 字段结构化业务动作审计，同时落 Redis Stream 和每日 JSONL。
>
> 追踪钩子接在主链路上，但 **OTLP exporter 是已实现、默认关闭**的可选能力，失败不影响业务，span 属性走白名单。

**打开这些文件**

- `monitoring/otel_tracer.py` → `OpenTelemetryTracer`、`MetricsCollector`（`/api/stats` 的计算字段在这里）
- `monitoring/otel_exporter.py` → `OTEL_EXPORT_ENABLED` 开关、`sanitize_attributes()`
- `monitoring/prometheus/alerts.yml` → 6 条规则；阈值全部标 `DESIGN_TARGET`
- `monitoring/grafana/dashboards/rag-overview.json`
- `common/audit.py` → 9 字段 schema、强制脱敏、request_id 关联、Redis Stream + 每日 JSONL
- `api/readiness.py` → 依赖感知的准入评估，fail closed

**边界**（这一节最容易被追问，逐条守住）：

- 指标端点、告警规则、Grafana JSON、审计、exporter 实现：`REPO_VERIFIED`。
- 带 Bearer token 的 Prometheus 抓取（无 token 401 / Bearer 200 / `up == 1`）：`LOCAL_REAL_VALIDATION`。
- **告警在生产触发过**：`PENDING`。**Grafana 面板被真实数据填充过**：`PENDING`。**应用 → exporter → collector → 后端 → 真的查到一条 span**：`PENDING`——这条闭环一次都没在本仓库跑通过，所以我只说 exporter 已实现，不说 tracing 已闭环。
- `monitoring/otel_tracer.py` 里还有一个更早的进程内 `AlertingManager`，**没有**接入 canonical 请求路径，属于遗留代码，不应把它当作告警方案。判定依据见 [repository-truth-audit.md](repository-truth-audit.md#two-alerting-mechanisms-and-which-one-is-canonical)。

---

## Evaluation

**说**

> 质量评测是隔离的可选链路：RAGAS harness、reporter、validator 和 golden set 都在，但 **RAGAS 不在默认依赖里**。
>
> 这里有个我特意做的设计：库级 `evaluate()` 保留 evaluator 不可用时的 fallback，而 `--require-ragas` 的 strict / real CLI 在缺依赖或缺凭据时 **fail fast**，返回非零并且不生成任何 quality report。也就是说，缺 evaluator 时永远不会被误读成"质量 0 分"。

**打开这些文件**

- `tests/evaluation/ragas_eval.py` → harness 与 `--require-ragas` 分支
- `tests/evaluation/validate_golden_set.py`、`tests/evaluation/golden_set.jsonl` → 集合校验
- `benchmarks/retrieval_benchmark.py` → 检索 benchmark 框架（`--list-configs` 实时探测，缺真实依赖时以 `BLOCKED` 记录原因，**不产出数字**）
- `benchmarks/performance.py` → 性能产物契约；未测量一律 `null`，**绝不写 `0`**
- `artifacts/benchmarks/README.md`、`artifacts/performance/README.md` → artifact 验收标准
- `docs/ragas-evaluation-guide.md`、`docs/benchmark-data-quality.md`

**边界**：评测**框架**是 `REPO_VERIFIED`；评测**结果**是 `PENDING`。当前没有经过验证的真实 RAGAS quality score，仓库里也没有任何可复现的检索或性能 artifact，所以**全仓库不引用任何检索指标数字，也不给本仓库报 QPS 或延迟**。golden set 的格式校验通过 ≠ 领域事实正确。

---

## Docker / Kubernetes

**说**

> **Docker Compose 是本仓库的 canonical 部署形态**。安全相关环境变量缺失时 Compose 会 fail-fast，这是有意行为：Redis 开 `requirepass`，Elasticsearch 开 `xpack.security.enabled`，还有 MinIO 凭据与服务间 token。
>
> `deploy/k8s/` 是**第二形态、非 canonical**，提供 API 网关的最小 Deployment / Service / ConfigMap / Secret 契约。探针是刻意分开的：startupProbe 打 `/api/health`（进程起来没有），livenessProbe 用 `tcpSocket`（依赖挂了不该触发重启），readinessProbe 打 `/api/ready`（依赖感知，不满足就 503 摘流量）。一个依赖在滚动更新期间不可用，不会把 Pod 拖进重启循环。

**打开这些文件**

- `docker-compose.yml` → canonical 形态
- `docker-compose.observability.yml` → 可选可观测叠加层；CI 有测试保证它是**纯增量**的（不启动 Prometheus / Jaeger / Grafana 时不影响 canonical 形态）
- `deploy/k8s/api-deployment.yaml` → 三种探针的分工与注释
- `docs/deployment-guide.md`、`docs/deployment-guide-k8s.md`
- `tests/deploy/test_k8s_manifests.py` → 31 项静态检查

**边界**：Compose 与 K8s 清单都是 `REPO_VERIFIED`（YAML 与契约校验）。**本仓库没有集群**，真实部署是 `PENDING`。演示可观测叠加层能启动 Prometheus / Jaeger / Grafana **不是**证据——启动它们不证明一条 span 到了。

---

## 失败 / 降级

**说**

> 降级路径是照着代码里真实存在的分支写的，不是通用模板：Redis 不可用 → 进程内会话 / 单进程限流；Elasticsearch 异常或有效文档不足 → Qdrant 异常时走 BM25-only 或空结果走 dense；Rewrite 失败 → 简单档位并抬高 Gate 阈值；KV 压力过高 → 截断、降级或拒绝。
>
> 每一个降级都会写进响应的 `degraded` / `fallback_reason` 字段，而不是静默。SLO Runbook 里的处置流程就是按这几条真实分支写的。

**打开这些文件**

- `core/pipeline_context.py` → `degraded` / `fallback_reason` 字段定义
- `core/pipeline.py` → `_fallback_rewrite()`、`_handle_rejection()`；降级赋值处
- `api/readiness.py` → 依赖不可用时 fail closed
- `docs/slo-runbook.md` → 5 个 SLO 目标（全部 `DESIGN_TARGET`）+ 8 条处置流程
- `tests/test_degradation_paths.py`

**边界**：降级分支与 Runbook 是 `REPO_VERIFIED`。**没有任何一个 SLO 目标被达成过**，5 个目标值全部是 `DESIGN_TARGET`；本仓库没有一次可复现的性能测量。

---

## 当前未验证边界

**说**（这一节主动讲，不要等被追问）

> 本项目把"实现了"和"验证过"分开标注，全仓库只用一套证据等级，并且用 `scripts/check_repo_consistency.py` 在 CI 里强制它不漂移。所以先说清楚**这里还什么都没有**的部分：
>
> - **性能数字**：仓库里没有可复现的 QPS / 延迟 benchmark artifact。一次都没测过。
> - **检索质量**：benchmark 框架在，但没有任何 artifact，所以我不报任何 Recall / NDCG。
> - **RAGAS 质量分**：harness 在，evaluator 依赖与凭据不可得，一次真实运行都没做。
> - **追踪闭环**：exporter 已实现且默认关闭，应用 → exporter → collector → 后端 → 查到 span 这条链一次都没跑通过。
> - **告警与 Grafana**：规则和 JSON 都在，但没有任何生产 Prometheus 评估过它们，仪表盘也从未导入过运行中的 Grafana。
> - **模型**：真实 4B / 14B vLLM GPU 拓扑在本仓库从未执行过——权重缺失、`vllm` 未安装。真实 BGE / CLIP / PaddleOCR smoke 同理。
> - **Qdrant**：当前可复现的回归覆盖是进程内 `QdrantClient(":memory:")`；开发沿革中确有真实本地 Qdrant + ES 的集成运行记录，但那次没有提交 artifact，所以不是可复现证据。两个方向都不能说错——既不能说"现在对着真实服务验证过"，也不能说"从来没有跑过真实服务"。
> - **生产拓扑**：Redis Cluster / Sentinel、云负载均衡、多节点 ES + TLS、长期 Prometheus/Grafana 运维、生产 HA 与 SLO，都不在本仓库。
>
> 历史生产规模的那些数字——3000+ 文档、5000+ 图片、200+ 用户、10–15 QPS、RTX A5000 ×2、年度技术创新奖——是 `HISTORICAL_PRODUCTION`，是上一家公司生产环境的事实，**不是**本仓库的 benchmark，本仓库也无法复现它们。奖项是公司认可，不是运行时技术验证。

**打开这些文件**

- `scripts/check_repo_consistency.py` → 证据一致性守卫本体；**这是最能说明工程习惯的一个文件**
- `docs/evidence-map.md` → 逐能力分级 + benchmark artifact 验收标准 + 已知表述风险清单
- `docs/repository-truth-audit.md` → 外部验证 tracker map；未验证项逐条列出
- `docs/validation/v2.5-runtime-security-validation.md` → 唯一 `LOCAL_REAL_VALIDATION` 的 5 项证据
- `tests/test_check_repo_consistency.py` → 守卫本身有测试

**边界**：`LOCAL_REAL_VALIDATION` **只有 5 项**（Redis 多进程会话、Redis 跨进程登录限流、nginx / `TRUSTED_PROXIES` 客户端 IP 解析、认证 Elasticsearch、带 Bearer token 的 Prometheus 抓取）。说"本地对真实依赖验证过"是安全的；说"生产集群 / HA / SLO 已验证"不是。

---

## 附：常见技术追问

| 追问 | 一句话回答 | 指向 |
|---|---|---|
| 「固定四路召回不是更简单？」 | 不是。简单问题只查 2 路，视觉问题才 4 路；多召回一路就多一路延迟和噪声，ES Fallback 是降级补召回不算一路 | [architecture-baseline.md](architecture-baseline.md) |
| 「Evidence Gate 的阈值怎么来的？」 | 来自 `config.json` 的设计值，Rewrite 降级时自动进入保守模式抬高阈值。**没有用真实标注数据标定过**，所以我不给拒答率 | `config.json` · `retrieval/evidence_gate.py` |
| 「ES 挂了会怎样？」 | 有效文档不足或 Qdrant 异常时走 BM25-only / 空结果走 dense，并把降级写进响应字段 | `retrieval/parallel_recall.py` · `core/pipeline.py` |
| 「为什么不用微服务？」 | 当前主线是单体；微服务目录是保留组件，端到端生产验证没有做过。单体在这个规模下更容易证明正确性 | [repository-truth-audit.md](repository-truth-audit.md) |
| 「这个项目最大的工程收获是什么？」 | 不是 RAG 本身，是**把证据等级做成机器可校验的契约**：文档写错话会让 CI 失败，而不是等到有人发现 | `scripts/check_repo_consistency.py` |
| 「这个仓库能直接跑起来吗？」 | API 可以 `python3 app.py` 起，Docker Compose 是 canonical 形态；但真实模型权重、真实检索栈、真实 GPU 拓扑不在仓库里，**跑起来不等于验证过** | [Quick Start](../README.md#quick-start) |