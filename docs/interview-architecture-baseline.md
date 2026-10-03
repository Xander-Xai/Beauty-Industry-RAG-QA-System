# 药妆助手面试架构唯一事实基线

更新时间：2026-10-02（v2.5 working milestone 的运行时/安全 reconciliation；`v2.5` 不是正式发布版本）

本文件是“整体架构”“几路召回”“如何重排”“如何控制幻觉”等面试问题的唯一事实基线。README、PRD、代码注释和历史面试稿发生冲突时，以当前主链路代码、配置和架构契约测试为准。

## 一句话结论

当前已验证架构是 FastAPI 单体主链路，不是已完成联调的微服务架构；在线检索采用动态 2 至 4 路召回，而不是固定四路：

- 简单问题：BGE-Dense + BM25，共 2 路。
- 复杂但不需要视觉信息的问题：BGE-Dense + BM25 + Rewrite 变体，共 3 路。
- 复杂且需要视觉信息的问题：BGE-Dense + BM25 + CLIP + Rewrite 变体，共 4 路。

ES Fallback 不算第五路。它是 Qdrant 异常或有效文档不足时使用的降级补召回；异步 CLIP 也不是新的一路，而是同一视觉检索能力对后续会话的预热。

## 事实等级

本节的等级就是 [Interview evidence map → Classification vocabulary](interview-evidence-map.md#classification-vocabulary)
中的 canonical evidence vocabulary，本文件不另立一套状态词。下面只补充每个等级在当前主链路里的面试表述。

| 等级 | 在当前主链路中的含义 | 面试表述 |
|---|---|---|
| `REPO_VERIFIED` | 已接入 `app.py -> api/routes.py -> core/pipeline.py`，有实现和契约测试 | 可以说“当前系统采用” |
| `REPO_VERIFIED`（实现）/ `PENDING`（真实资产） | 有实现和契约测试，但需要真实基础设施、模型权重进一步验收 | 可以说“系统已实现，生产效果需部署验收” |
| `REPO_VERIFIED`（可选能力，未接入主链路） | 代码或配置存在，但当前默认未开启或未接入主链路 | 只能说“支持”或“预留” |
| `PENDING` | 缺少有效运行结果或生产数据 | 不能说“已经上线验证” |

## 当前主链路

```text
FastAPI API
  -> 身份解析与 L1/L2 缓存
  -> Query Rewrite 与复杂度判断并行执行
  -> 动态 2 至 4 路召回
  -> 权限过滤与动态加权 RRF
  -> BiEncoder 宽保留
  -> 双 CrossEncoder 精排
  -> Evidence Gate
  -> 4B/14B 模型路由与生成
  -> Answer Gate
  -> 返回答案、证据和审计信息
```

默认联调入口是 FastAPI 单体后端和 React 前端。仓库中的微服务目录属于保留能力，尚不能替代当前单体主线来回答“现有系统架构”。

## 离线知识构建

离线侧将文档和图片加工成统一知识资产：

1. 文档解析、清洗、切分并补充来源、业务类型、状态、知识版本和权限掩码。
2. 图片执行 OCR 文本提取和 CLIP 图像向量化。
3. BGE 文本向量与 CLIP 图像向量写入 Qdrant，文本内容写入 Elasticsearch。
4. 调度器通过文件状态和内容哈希支持增量更新，并支持显式切换知识版本。

BLIP 不应描述为固定的离线建库步骤。当前主链路是在视觉查询命中图像结果后在线按需生成描述；模型或权重不可用时跳过。

## 在线检索与权限

四个可选召回路径分别是：

| 路径 | 存储与模型 | 触发条件 |
|---|---|---|
| `dense_bge` | BGE + Qdrant 文本集合 | 所有问题 |
| `bm25_es` | BM25 + Elasticsearch | 所有问题 |
| `clip_visual` | CLIP + Qdrant 图像集合 | 复杂问题中被视觉判别器选中的请求 |
| `rewrite_variant` | Query Rewrite 变体 + BGE + Qdrant | 复杂问题 |

结果通过加权 RRF 融合。法规问题提高 BM25 权重，视觉相关问题提高 CLIP 权重；配置中的 `w_text`、`w_clip` 会映射到实际路径名。

权限采用双层控制：

- Qdrant 下推文档状态；显式启用知识版本后同时下推版本条件。
- Elasticsearch 下推状态、知识版本、角色和部门过滤。
- 所有召回适配器保留角色和部门元数据，融合前统一通过位掩码 RBAC 二次过滤。
- ES Fallback 复用正常 BM25 的权限查询，不能绕过权限。
- 缓存键包含知识版本和权限指纹，公开数据才进入进程内 L1，其他权限组合使用 Redis L2。

## 重排、证据门控与生成

RRF 后先由 BiEncoder 宽保留 Top 150，再由两个 CrossEncoder 集成精排到 Top 10。当前在线调用使用请求内批量预测；跨请求异步聚合接口虽然存在，但没有接入这条主调用，因此面试时不要说“当前主链路已经完成跨请求动态微批”。

生成前的 Evidence Gate 综合以下四项：

- CrossEncoder Top 1 分数；
- Top 3 平均分；
- 多路召回一致性；
- Top 3 文档间一致性。

它给出正常生成、增强证据后生成或拒答三种决策。生成后的 Answer Gate 再检查答案与核心证据的一致性，法规类矛盾会拒答。

生成拓扑当前是**单一共享 4B vLLM 端点**（`gpu1.models.vllm_4b`，端口 8101）：Query Rewrite 与简单生成共用 `gen_4b`，复杂请求走 `gen_14b`（Qwen3-14B）。旧 PRD 中“独立的 vLLM-Rewrite + vLLM-Gen-4B 双实例”是历史/目标设计，不是当前实现。KV 压力过高时可以截断、降级或拒绝。当前 `development` 配置会把复杂模型端点降级到 4B，且仓库不包含完整模型权重，因此“双模型已完成生产压测”不属于当前事实。

## v2.5 运行时与安全契约

> **版本语义**：`v2.5` 是本节契约的历史 working milestone / development-phase 标签，**不是**正式发布版本。当前 canonical runtime version 为 `config.json` → `system.version` = `2.3.0`，`CHANGELOG.md` 最新正式 release 亦为 `2.3.0`，其后的变更记在 `[Unreleased]`。

- **会话状态**：`SessionState` 在配置 Redis 时跨 worker 持久化（TTL 7200s），Redis 不可用时降级进程内内存。序列化使用稳定 schema，`QueryRewriteResult`/`RecallResult` 会重建。真实 Redis 多进程行为已在本地完成 `LOCAL_REAL_VALIDATION`（写入进程 A、进程 B 类型化恢复、进程 C 观察到更新、TTL 刷新）；Redis Cluster/Sentinel 生产拓扑仍属外部验证边界。
- **登录限流**：5 次/分钟；多 worker 走 Redis 计数，Redis 不可用降级单进程内存。仅当 TCP 对端属于 `TRUSTED_PROXIES` 时才信任 `X-Forwarded-For`，否则客户端伪造 XFF 无法绕过限流。真实 Redis 跨进程限流与真实 nginx 反向代理客户端 IP 解析均已完成 `LOCAL_REAL_VALIDATION`。
- **可观测端点**：`GET /api/stats` 与 `GET /api/metrics` 需要认证（`require_identity`）；`GET /api/health` 公开。Prometheus 抓取需 Bearer token；本地已用真实 Prometheus 完成认证抓取验证（无 token 401、Bearer 200、target `up == 1`）。
- **Elasticsearch 安全**：Compose 启用 `xpack.security.enabled=true`，在线/离线客户端优先读取环境凭据；本地已用真实认证 ES 8.11 验证（匿名/错误凭据 401、writer mapping + `search_after`、在线 BM25 检索）。
- **知识版本激活**：构建/校验/封存可自动化，但**激活 `knowledge_version_epoch` 是显式人工发布步骤**，没有自动 activation。
- **本地真实验证等级（LOCAL_REAL_VALIDATION）**：上述 Redis 多进程、nginx / `TRUSTED_PROXIES`、认证 ES、认证 Prometheus 抓取均已在本地真实依赖上执行，证据见 [v2.5 working-milestone runtime/security validation](validation/v2.5-runtime-security-validation.md)。这**不等于**生产集群验证。
- **仍属外部验证边界**：Redis Cluster/Sentinel 生产拓扑、云负载均衡拓扑、多节点 ES/TLS、长期 Prometheus/Grafana 运维、生产 HA/SLO，以及单 4B / 14B vLLM GPU 部署，均未在本仓库验证。

## 可观测性与评测边界

面试时最重要的一句区分：**「实现了」不等于「生产验证过」**。下面按 canonical 证据等级分层，不要跨层表述。

### REPO_VERIFIED（本仓库代码 + 确定性测试覆盖）

| 能力 | 实现位置 | 说明 |
|---|---|---|
| Prometheus 指标端点 | `api/routes.py` → `monitoring/otel_tracer.py::MetricsCollector` | `/api/metrics` 暴露 `rag_*` series；`/api/stats` 暴露计算字段 |
| 结构化企业动作审计 | `common/audit.py` | 统一 9 字段 schema、强制脱敏、request_id 关联、Redis Stream + 每日 JSONL |
| 性能产物框架 | `benchmarks/performance.py`、`artifacts/performance/` | 七文件契约；未测量即 `null`，绝不写 `0`；状态由实际观测推导 |
| Prometheus 告警规则 | `monitoring/prometheus/alerts.yml` | 6 条规则，只引用真实 emit 的 series |
| Grafana 仪表盘 JSON | `monitoring/grafana/dashboards/rag-overview.json` | 10 个面板，只用真实指标 |
| SLO 与故障 Runbook | `docs/slo-runbook.md` | 5 个目标 + 8 个处置流程，按代码中真实存在的降级路径编写 |
| OTLP exporter 实现 | `monitoring/otel_exporter.py` | 可选启用、失败不影响业务、span 属性白名单 |

### LOCAL_REAL_VALIDATION（本地单主机、真实依赖上跑过）

只有这五项，不要扩大：

- Redis 多进程会话持久化
- Redis 跨进程登录限流
- nginx / `TRUSTED_PROXIES` 反向代理客户端 IP 解析
- 认证 Elasticsearch（在线 BM25 + 离线 writer）
- 带 Bearer token 的 Prometheus 抓取（无 token 401 / Bearer 200 / target `up == 1`）

### DESIGN_TARGET（写进文档的目标值，没有任何实测支撑）

- SLO 全部目标值（99.5% / <1% / ≤2000ms / ≥99% / 100%）
- Prometheus 告警阈值（6 条规则里的每一个数字）
- 容量阈值（例如 `RagRequestSaturation` 的在途请求上限 50）
- PRD 的 P95≈2s / P99≤3s / QPS 12–18 —— 均为推导模型，不是实测

### PENDING（代码在，但缺的资产/凭据/运行记录在这里拿不到）

- **OTLP 运行期闭环 — PENDING**：应用 → exporter → collector → 后端 → 查到 span 这条链路没有任何运行记录；exporter 已实现且默认关闭
- 真实性能产物 — PENDING：仓库内没有 artifact，因此 QPS/P95/P99 一次都没测过
- 告警在生产触发 — PENDING：规则存在，但没有任何生产 Prometheus 评估过它们
- Grafana 面板被真实数据填充 — PENDING：JSON 从未导入过运行中的 Grafana
- 真实 RAGAS 质量结果 — PENDING：无获批 evaluator 凭据
- 真实 4B/14B vLLM GPU 拓扑 — PENDING：权重不在仓库，`vllm` 未安装
- 真实检索 benchmark 结果 — PENDING：框架已实现，无可复现 artifact
- 真实 BGE / CLIP / PaddleOCR smoke — PENDING：`EXTERNAL_MODEL_ASSET_REQUIRED`

### 追踪钩子的准确说法

追踪钩子在主链路（`core/pipeline.py` → `monitoring/otel_tracer.py`），默认走 OpenTelemetry SDK
`TracerProvider`；只有 OTel SDK 未安装或 provider 初始化失败时，才退回本地内存 span 模式。
导出是**已实现但默认关闭**的可选能力：`OTEL_EXPORT_ENABLED=false` 时不挂载
任何 span processor，span 既不导出也不保留；exporter 包本身是 `requirements-otel.txt` 里的
可选依赖。导出是否生效可以看 `rag_otel_exporter_enabled`。

所以只能说：**「追踪钩子已接入，OTLP exporter 已实现且默认关闭，运行期闭环是 PENDING」**。
不能说「OpenTelemetry/Jaeger 导出已闭环」「tracing 已验证」「Jaeger 已上线」——
本仓库没有任何一个 span 被后端查询到过。旧 PRD / 旧计划里的 Jaeger thrift agent 路径
已经移除：它从未有过 canonical 消费者，当前 exporter 走 OTLP，不走 Jaeger agent。

### 两套告警机制不要混淆

- **正式告警契约**：`monitoring/prometheus/alerts.yml`，由外部 Prometheus 加载评估。
- **`monitoring/otel_tracer.py::AlertingManager`**：更早的进程内阈值引擎，**没有**接入
  canonical 请求路径，`config.json` 里喂它的 `alerting.rules` 配置块也已移除。它是遗留代码，
  不是 Prometheus 规则的一部分，面试时不要提。

### 评测

RAGAS harness / reporter / validator 与 Golden Set 已存在：最初 seed 27 条，现 300+ 条，实际条数以
`validate_golden_set` 输出为准。**格式校验通过 ≠ 领域事实正确**。

RAGAS 是隔离的可选 evaluator，不在默认依赖中。库级 `evaluate()` 保留 evaluator-unavailable fallback（该结果不是质量结果）；使用 `--require-ragas` 运行 strict / real evaluator CLI 时，缺少 evaluator dependency 或 evaluator credential 会 **fail fast**：返回非零状态且不生成任何 quality report。当前没有经过验证的真实 RAGAS quality score；没有生产反馈数据时，不应声称阈值已经由线上反馈自动学习或每周稳定更新。

## Q12 标准回答

我会把药妆助手概括为一套多模态知识底座、离线知识构建和在线 RAG 问答两条核心链路，再加上权限、安全与可观测性保障。

离线侧负责解析、清洗和切分法规、配方、原料、产品文档，并为图片执行 OCR 和 CLIP 向量化。BGE 文本向量与 CLIP 图像向量写入 Qdrant，文本同步写入 Elasticsearch 支持 BM25；每个知识块都带来源、状态、知识版本以及角色和部门权限掩码，调度器通过文件状态和内容哈希支持增量更新。BLIP 是在线视觉结果命中后的按需增强，不是固定离线步骤。

在线侧由 FastAPI 接收请求，先解析身份并查询带知识版本和权限指纹的 L1/L2 缓存。缓存未命中后，Query Rewrite 与复杂度判断并行执行。检索不是固定四路，而是动态 2 至 4 路：简单问题走 BGE 和 BM25；复杂问题增加 Rewrite 变体；复杂且视觉相关时再增加 CLIP。多路结果先经过文档级 RBAC 二次过滤，再使用动态加权 RRF 融合。ES Fallback 是召回不足或 Qdrant 异常时的降级策略，不算第五路。

融合结果经过 BiEncoder 宽保留和双 CrossEncoder 精排。生成前，Evidence Gate 综合 Top 1、Top 3、多路一致性和文档间一致性，决定正常生成、增强证据生成或拒答；生成后，Answer Gate 再校验答案是否忠于核心证据，法规类矛盾会直接拒答。

生成层使用单一共享 4B vLLM 端点处理 Query Rewrite 与简单生成，复杂请求路由到 Qwen3-14B，并结合 KV 压力做截断、降级或拒绝。

工程侧我分三层说，因为这三层的证据强度不一样。**已实现并有测试覆盖**的是：指标端点、结构化业务动作审计、性能产物契约、Prometheus 告警规则、Grafana 仪表盘 JSON、SLO 与故障 Runbook，以及一个默认关闭的 OTLP exporter。**在本地真实依赖上验证过**的只有 Redis 多进程、nginx 代理信任、认证 Elasticsearch 和认证 Prometheus 抓取。**还没有证据**的是：真实性能数字、告警在生产触发、Grafana 面板被真实数据填充，以及 exporter 到 collector 到后端再查到 span 的完整闭环——这个闭环我一次都没跑通过，所以只能说 exporter 已实现，不能说 tracing 已闭环。另外 `config.json` 里的延迟和 QPS 目标值是设计目标，不是实测。

一句话总结：这是一套以 Qdrant 和 Elasticsearch 为多模态知识底座、以动态 2 至 4 路召回和两级重排保障检索质量、以双层 Gate 和 RBAC保障可信与权限安全的企业内部 RAG 系统；运维证据闭环已经建好，但真实运行数据还需要在部署环境补齐。

## 防止答案再次漂移

每次修改召回、权限、路由或评测配置后，至少执行：

```bash
pytest -q tests/test_architecture_contract.py
python3 -m tests.evaluation.validate_golden_set \
  --dataset tests/evaluation/golden_set.jsonl
```

面试稿只从本文件复制。新增能力必须先接入当前主链路并补充契约测试，再把表述从“支持”升级为“当前采用”。
