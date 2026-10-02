# 药妆助手面试架构唯一事实基线

更新时间：2026-10-02（v2.5 运行时/安全 reconciliation）

本文件是“整体架构”“几路召回”“如何重排”“如何控制幻觉”等面试问题的唯一事实基线。README、PRD、代码注释和历史面试稿发生冲突时，以当前主链路代码、配置和架构契约测试为准。

## 一句话结论

当前已验证架构是 FastAPI 单体主链路，不是已完成联调的微服务架构；在线检索采用动态 2 至 4 路召回，而不是固定四路：

- 简单问题：BGE-Dense + BM25，共 2 路。
- 复杂但不需要视觉信息的问题：BGE-Dense + BM25 + Rewrite 变体，共 3 路。
- 复杂且需要视觉信息的问题：BGE-Dense + BM25 + CLIP + Rewrite 变体，共 4 路。

ES Fallback 不算第五路。它是 Qdrant 异常或有效文档不足时使用的降级补召回；异步 CLIP 也不是新的一路，而是同一视觉检索能力对后续会话的预热。

## 事实等级

| 等级 | 含义 | 面试表述 |
|---|---|---|
| 当前主链路 | 已接入 `app.py -> api/routes.py -> core/pipeline.py` | 可以说“当前系统采用” |
| 代码与测试已实现 | 有实现和契约测试，但需要真实基础设施、模型权重进一步验收 | 可以说“系统已实现，生产效果需部署验收” |
| 可选能力 | 代码或配置存在，但当前默认未开启或未接入主链路 | 只能说“支持”或“预留” |
| 未闭环 | 缺少有效运行结果或生产数据 | 不能说“已经上线验证” |

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

- **会话状态**：`SessionState` 在配置 Redis 时跨 worker 持久化（TTL 7200s），Redis 不可用时降级进程内内存。序列化使用稳定 schema，`QueryRewriteResult`/`RecallResult` 会重建。真实 Redis 多进程行为已在本地完成 `LOCAL_REAL_VALIDATION`（写入进程 A、进程 B 类型化恢复、进程 C 观察到更新、TTL 刷新）；Redis Cluster/Sentinel 生产拓扑仍属外部验证边界。
- **登录限流**：5 次/分钟；多 worker 走 Redis 计数，Redis 不可用降级单进程内存。仅当 TCP 对端属于 `TRUSTED_PROXIES` 时才信任 `X-Forwarded-For`，否则客户端伪造 XFF 无法绕过限流。真实 Redis 跨进程限流与真实 nginx 反向代理客户端 IP 解析均已完成 `LOCAL_REAL_VALIDATION`。
- **可观测端点**：`GET /api/stats` 与 `GET /api/metrics` 需要认证（`require_identity`）；`GET /api/health` 公开。Prometheus 抓取需 Bearer token；本地已用真实 Prometheus 完成认证抓取验证（无 token 401、Bearer 200、target `up == 1`）。
- **Elasticsearch 安全**：Compose 启用 `xpack.security.enabled=true`，在线/离线客户端优先读取环境凭据；本地已用真实认证 ES 8.11 验证（匿名/错误凭据 401、writer mapping + `search_after`、在线 BM25 检索）。
- **知识版本激活**：构建/校验/封存可自动化，但**激活 `knowledge_version_epoch` 是显式人工发布步骤**，没有自动 activation。
- **本地真实验证等级（LOCAL_REAL_VALIDATION）**：上述 Redis 多进程、nginx / `TRUSTED_PROXIES`、认证 ES、认证 Prometheus 抓取均已在本地真实依赖上执行，证据见 [v2.5 runtime/security validation](validation/v2.5-runtime-security-validation.md)。这**不等于**生产集群验证。
- **仍属外部验证边界**：Redis Cluster/Sentinel 生产拓扑、云负载均衡拓扑、多节点 ES/TLS、长期 Prometheus/Grafana 运维、生产 HA/SLO，以及单 4B / 14B vLLM GPU 部署，均未在本仓库验证。

## 可观测性与评测边界

- Prometheus 风格指标、健康检查与系统统计已接入在线主链路。
- OpenTelemetry 追踪钩子位于主链路（`core/pipeline.py` → `monitoring/otel_tracer.py`）。默认依赖会安装 `opentelemetry-sdk`，但 `config.json` → `monitoring.jaeger.enabled=false` 且没有配置任何 exporter，此时 tracer 走 OTel SDK provider 且没有 exporter（span 既不导出也不保留）；只有 OTel SDK 未安装或初始化失败时才退回本地内存 span 模式。因此只能说“追踪钩子已接入主链路”，不能说“OpenTelemetry/Jaeger 导出已闭环”，也不能把默认 0 值的指标当作已闭合生产指标。
- Jaeger 是可选导出器，当前配置默认关闭，不能说成默认运行。
- RAGAS harness / reporter / validator 与 Golden Set 已存在：最初 seed 27 条，现 300+ 条，实际条数以 `validate_golden_set` 输出为准。**格式校验通过 ≠ 领域事实正确**。
- RAGAS 是隔离的可选 evaluator，不在默认依赖中。库级 `evaluate()` 保留 evaluator-unavailable fallback（该结果不是质量结果）；使用 `--require-ragas` 运行 strict / real evaluator CLI 时，缺少 evaluator dependency 或 evaluator credential 会 **fail fast**：返回非零状态且不生成任何 quality report。当前没有经过验证的真实 RAGAS quality score；没有生产反馈数据时，不应声称阈值已经由线上反馈自动学习或每周稳定更新。

## Q12 标准回答

我会把药妆助手概括为一套多模态知识底座、离线知识构建和在线 RAG 问答两条核心链路，再加上权限、安全与可观测性保障。

离线侧负责解析、清洗和切分法规、配方、原料、产品文档，并为图片执行 OCR 和 CLIP 向量化。BGE 文本向量与 CLIP 图像向量写入 Qdrant，文本同步写入 Elasticsearch 支持 BM25；每个知识块都带来源、状态、知识版本以及角色和部门权限掩码，调度器通过文件状态和内容哈希支持增量更新。BLIP 是在线视觉结果命中后的按需增强，不是固定离线步骤。

在线侧由 FastAPI 接收请求，先解析身份并查询带知识版本和权限指纹的 L1/L2 缓存。缓存未命中后，Query Rewrite 与复杂度判断并行执行。检索不是固定四路，而是动态 2 至 4 路：简单问题走 BGE 和 BM25；复杂问题增加 Rewrite 变体；复杂且视觉相关时再增加 CLIP。多路结果先经过文档级 RBAC 二次过滤，再使用动态加权 RRF 融合。ES Fallback 是召回不足或 Qdrant 异常时的降级策略，不算第五路。

融合结果经过 BiEncoder 宽保留和双 CrossEncoder 精排。生成前，Evidence Gate 综合 Top 1、Top 3、多路一致性和文档间一致性，决定正常生成、增强证据生成或拒答；生成后，Answer Gate 再校验答案是否忠于核心证据，法规类矛盾会直接拒答。

生成层使用单一共享 4B vLLM 端点处理 Query Rewrite 与简单生成，复杂请求路由到 Qwen3-14B，并结合 KV 压力做截断、降级或拒绝。工程侧提供健康检查、指标、审计和可选 OpenTelemetry/Jaeger 追踪，`/api/stats` 与 `/api/metrics` 需要认证。需要说明的是，当前仓库默认是开发模式，模型权重、完整基础设施和有效 RAGAS 运行结果仍需在部署环境验收。

一句话总结：这是一套以 Qdrant 和 Elasticsearch 为多模态知识底座、以动态 2 至 4 路召回和两级重排保障检索质量、以双层 Gate 和 RBAC 保障可信与权限安全的企业内部 RAG 系统。

## 防止答案再次漂移

每次修改召回、权限、路由或评测配置后，至少执行：

```bash
pytest -q tests/test_architecture_contract.py
python3 -m tests.evaluation.validate_golden_set \
  --dataset tests/evaluation/golden_set.jsonl
```

面试稿只从本文件复制。新增能力必须先接入当前主链路并补充契约测试，再把表述从“支持”升级为“当前采用”。
