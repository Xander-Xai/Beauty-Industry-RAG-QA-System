# Document Type: Product / Architecture Design

**Implementation Status:** mixed — this document records goals and design proposals as well as capabilities whose code has since been added. A PRD statement is not evidence of implementation.

**Canonical Runtime Status:** [README.md](README.md) + [docs/repository-truth-audit.md](docs/repository-truth-audit.md).

> **How to read this document.** Every section below carries a status marker. Nothing in this
> PRD is evidence on its own; the marker tells you which claim to read it as.
>
> | Marker | Meaning |
> |---|---|
> | `CURRENT` | Implemented in the canonical path and covered by collected tests. Levels: `REPO_VERIFIED`, or a compound `REPO_VERIFIED` (implementation) / `PENDING` (runtime) |
> | `DESIGN_TARGET` | A target or design recorded here. No implementation, or no reproducible benchmark. `DESIGN_TARGET` in every case |
> | `HISTORICAL_DESIGN` | Superseded design retained for lineage. Not the current architecture; kept because the reasoning is still useful |
> | `PENDING_VALIDATION` | Implemented, but validating it needs an asset, runtime or credential this repository does not have |
>
> Evidence levels themselves are defined once, in
> [docs/evidence-map.md → Classification vocabulary](docs/evidence-map.md#classification-vocabulary).
> This document does not introduce a second vocabulary.

| Topic | Current classification | Evidence / boundary |
|---|---|---|
| Offline ingestion and OCR | Implemented (external validation pending) | `offline/` implements TXT/PDF/DOCX/XLSX parsing, OCR/image pipeline, BGE/CLIP adapters, Qdrant text/image and Elasticsearch writers, incremental/carry-forward/full-rebuild, validator, epoch seal, scheduler and feedback; real model/OCR smoke is pending external assets (see audit) |
| Airflow ingestion | Implemented in code; runtime optional | Config-driven DAGs register only when Airflow and offline modules are available; no real Airflow execution is verified and default Compose does not run Airflow |
| RRF | Implemented in code | Fusion implementation exists; production relevance/quality is not implied |
| BiEncoder | Partial | Reranker and pipeline integration exist; model assets and evaluation are separate |
| RAGAS | Partial | Harness source and data exist; package is excluded from default dependencies pending an upstream security fix; no quality threshold is certified |
| QLoRA | Partial | Training utility and example data exist; trained output and reproducible result are not included |
| AdapterManager | Partial | PEFT lifecycle code integrates with `LLMClient`; actual loading requires configuration, dependencies and adapter assets |
| RBAC | Partial | Runtime auth/RBAC code exists and enforces a uint32 mask contract with fail-closed handling of missing/malformed metadata; deployment policy and end-to-end access still require verification |
| Cache | Partial | Cache implementations and metrics exist; the complete PRD invalidation design is not certified |
| KV admission | Partial | Admission control code exists; capacity behavior requires workload-specific measurement |
| Performance metrics | Design targets | Numerical latency/QPS claims below have no benchmark artifact and are not verified production results |
| Performance evidence artifact | Implemented in code; no run yet | `benchmarks/performance.py` implements a seven-file contract where an unexecuted run records `null` rather than `0` and carries a `blocked_reason`; `artifacts/performance/` contains only its README, so no QPS/P95/P99 has been measured |
| Structured audit trail | Implemented in code | `common/audit.py` emits business-action events with a stable 9-field schema, forced redaction and request-id correlation; persisted to Redis Stream + daily JSONL. **Nine** action types are registered in `KNOWN_ACTIONS`: `auth.login.success`, `auth.login.failure`, `auth.login.rate_limited`, `admin.user.create`, `admin.role.update`, `media.access.denied`, `knowledge.epoch.seal`, `knowledge.source.trust_decision`, `knowledge.source.quarantine`. No SIEM forwarding, and no `knowledge.epoch.activate` event because no activate endpoint exists |
| Alerting | Implemented as configuration | `monitoring/prometheus/alerts.yml` defines 6 alerts over metrics the canonical collector actually emits; every threshold is a `DESIGN_TARGET`. No production Prometheus evaluates them and no alert has fired in production |
| SLO / runbook | Documented; objectives are targets | `docs/slo-runbook.md` defines 5 objectives and 8 incident procedures against degradation paths that exist in code. No objective has been met or measured |
| OpenTelemetry export | Exporter implemented and test-covered; disabled by default; runtime closed loop pending | `monitoring/otel_exporter.py` adds an opt-in OTLP path with non-fatal failure semantics and a span-attribute allow-list; with `OTEL_EXPORT_ENABLED=false` no span processor is attached, and the exporter package is an optional dependency in `requirements-otel.txt`. Application -> exporter -> collector -> backend -> queried span is `PENDING` |
| In-process alert engine | Legacy; not wired into the canonical request path | `monitoring/otel_tracer.py` contains an older in-process `AlertingManager` that no module under `app.py`, `api/` or `core/` constructs. It is not the Prometheus rule set and must not be described as the alert contract; the unconsumed `config.json` -> `alerting.rules` block that fed it was removed |

All performance figures below are **design targets or model estimates**, not verified production measurements, unless linked to a reproducible benchmark artifact. Superseded implementation plans, formerly stored under `docs/superpowers/`, were removed from this branch and are preserved only in Git history; they are not current implementation evidence.

化妆品企业级多模态 RAG 智能问答系统（双卡版）

> **注意**：本文档为系统设计阶段的 PRD，描述的是**双卡目标架构**（双 GPU + 微服务）。当前仓库已验证的主线为 **FastAPI 单体后端**（`app.py`），微服务目录保留但尚未完成全链路契约对齐。具体实现以 [`README.md`](README.md) 中的「当前已验证主线」为准。

> **版本语义**：本 PRD 与仓库内部分文档使用 “v2.5” 标签。该标签是 **历史 working milestone / development-phase 标签**，**不是**正式发布版本，也不是当前 canonical runtime version。canonical runtime version 为 `config.json` → `system.version` = `2.3.0`；`CHANGELOG.md` 中存在 `2.3.0` release entry，其后的变更归入 `[Unreleased]`。当前 GitHub Release / tag 状态以仓库实际状态为准，详见[版本策略](docs/repository-truth-audit.md#version-policy)。

> **Runtime reconciliation（v2.5 working milestone）**：以下 PRD 设计与当前实现不一致；正文保留设计意图，但**不得作为当前实现证据**。
>
> | PRD 章节 | PRD 设计 | 当前实现（code/config/tests） |
> |---|---|---|
> | §4.2 / §5.1 | 历史/目标设计：双 vLLM 实例（独立的 vLLM-Rewrite 与 vLLM-Gen-4B，端口 8101/8102） | **单一共享 4B 端点** `gpu1.models.vllm_4b`（端口 8101）；rewrite 与简单生成共用 `gen_4b` endpoint，复杂查询走 `gen_14b`（Qwen3-14B） |
> | §3.6 / §10.5 | `knowledge_version_epoch` 由 Airflow 自动 bump / 切换 active epoch | 构建（build/validate/seal）可自动化；**激活 epoch 是显式人工发布动作**，未实现自动 activation |
> | §13 / §4.1 | 微服务拆分（`/rewrite`、`/generate`、`api-gateway/`）为部署单位 | 已验证主线为 FastAPI 单体 `app.py`；六个微服务目录（`api-gateway/`、`retrieval-service/`、`generation-service/`、`monitoring-service/`、`cache-service/`、`rewrite-service/`）保留但未完成与当前前端的全链路契约对齐 |
> | §2 / §5 / §7 | 延迟/QPS/吞吐数字 | 无 benchmark artifact，均为**设计目标/模型估算**，不是实测生产结果 |
> | §5.2.5 / §9 | KV 压力降级阈值 70% / 80% / 90% / 95% | `admission/kv_admission.py` 的实际常量是 `THRESHOLD_TRUNCATE=0.80`、`THRESHOLD_SOFT_STOP=0.90`、`THRESHOLD_CRITICAL=0.95`（另有更早的 `threshold_tighten`）。**以代码为准；本 PRD 这两个阈值区间已过时** |
> | §6 | BLIP 触发率 < 5% | `models/blip_service.py` 的模块文档记录 ~15%，且触发信号是 `max(keyword_rule_score, bert_classifier_score, has_image_hit)` **三路**取最大——本 PRD 的「双路」表述已过时 |
> | §10.2 | Prompt Registry 由 DB 管理版本，自动递增 | **机制已实现，载体不是 DB**：版本存文件目录 `./data/feedback/rewrite/prompt_versions`，`save_prompt_version()` 自动生成版本号，`activate_prompt_version()` 自动改写 `config.json` 的 `generation.prompt_version` |
> | §10.2 | embedding_version 用模型 commit SHA / revision 保证换权重即失效 | 缓存键只用 `embedding.text.model_path`；**不含** `model_revision`，**不含 CLIP 配置**。同路径换权重不会失效缓存——已知实现缺口 |
> | §4.6 | 不做尾续写，采用「上下文重建 + 单次重生成」 | 实现是 **assistant 前缀回放 + 约束指令续写 + 两段拼接**（`generate_continuation()` → `merged_answer = a + b`）；`temperature=0` 已实现，`logit_bias` 未实现 |
> | §12 | 指标清单 | `/api/metrics` 实际 emit 约 30 个 series（含 evidence/nli/blip/prefix-cache/admission/kv_pressure），比率一律是 `/api/stats` 计算字段或 PromQL 派生；但告警只引用 6 个、面板只引用 5 个，其余**无人消费** |
>
> **逐节状态索引**：下表给出每个章节的可引用状态，避免把设计稿整段读成当前实现。
>
> | 章节 | 状态 | 依据 / 边界 |
> |---|---|---|
> | §1 项目背景 | `CURRENT` | 业务问题陈述 |
> | §2 系统目标与指标（延迟 / 有效并发 / QPS / 输出长度 / 多轮保留） | `DESIGN_TARGET` | 无 benchmark artifact。P95≈2s、P99≤3s、QPS 12–18、有效并发 20–25 全部是推导模型，见 §2.2 |
> | §2.1 端到端延迟拆解 | `DESIGN_TARGET` | 逐阶段区间是模型估算，一次都没测过 |
> | §2.2 吞吐模型 | `DESIGN_TARGET` | 公式自洽，但输入量（KV 预算、token 分布）未经 workload 验证 |
> | §3.1 数据范围（500+ 文档 baseline） | `HISTORICAL_DESIGN` | 设计阶段规模基线，**不是**当前可复现规模，也不是历史生产规模 |
> | §3.2 文档处理 | `CURRENT`（字符切块已实现）+ `HISTORICAL_DESIGN`（JSON Lines 中间产物未采用） | 默认 500 字符 / 10% 重叠；`doc_type`/`page_number`/权限掩码注入均已实现 |
> | §3.3 图像处理 | `CURRENT`（OCR provider 接口 + CLIP 512d + 视觉权重）+ `HISTORICAL_DESIGN`（`image_ocr` 独立 embedding_type 未采用） | PaddleOCR 为可选外部运行时，默认不安装 → 见 PENDING_VALIDATION |
> | §3.4 向量存储与权限标签 | `CURRENT` | `rag_text_768` / `rag_image_512` 双集合 + uint32 掩码；仓库未声明任何 IVF/ANN 调优参数 |
> | §3.5 Bitmask 编码规则 | `CURRENT` | `common/auth.py::is_allowed` 与本文伪代码一致；uint32 边界与 fail-closed 在 `_validate_permission_mask_claim` / `is_document_authorized` |
> | §3.6 文档生命周期与版本化 | `CURRENT`（epoch 构建/校验/封存）+ `HISTORICAL_DESIGN`（定时任务自动 archived、前端历史区间查询） | 激活 epoch 是人工动作；`expiry_date` 运行时判断已不再使用 |
> | §3.7 离线调度 | `CURRENT`（cron/Airflow/CLI 共用业务逻辑）+ `PENDING_VALIDATION`（真实 Airflow 执行） | DAG 仅在 Airflow 与离线模块可发现时注册；默认 Compose 不启动 Airflow |
> | §4.1 核心链路 | `CURRENT` | 在线顺序与 `core/pipeline.py` 一致 |
> | §4.2 执行层隔离（双 vLLM 实例） | `HISTORICAL_DESIGN` | 当前是单一共享 4B 端点 + 14B，见上方 reconciliation |
> | §4.3 模型分级路由 | `CURRENT`（路由契约）+ `PENDING_VALIDATION`（真实 GPU） | BERT 复杂度判别 → 4B / 14B；训练产物需单独生成 |
> | §4.4 Query Rewrite | `CURRENT`（降级链、JSON Schema、兜底档位）+ `DESIGN_TARGET`（每周 gold set 评估并自动更新 prompt 与 BERT 阈值） | 失败降级为 HTTP 200 结构化拒答，不返回 503，已实现；**数据闭环的自动更新未实现** |
> | §4.5 双 Embedding 路由与 CLIP 策略 | `CURRENT`（判别路由、同步/异步 CLIP、超时丢弃、删除 CLIP embedding cache）+ `DESIGN_TARGET`（融合权重由日志学习、每周 A/B 校准） | 权重初值来自 `config.json`；**无自动学习代码** |
> | §4.6 长文本一致性（上下文重建 + 证据锁定） | `CURRENT` | 不做尾续写；`has_more` / `session_id` 契约已实现 |
> | §4.7 动态输出长度 | `CURRENT`（按业务类型分级）+ `CURRENT`（KV 联动截断） | `admission/kv_admission.py::get_effective_max_tokens()` 返回 action 而不改 `max_tokens`，Prefix Caching 保护已实现 |
> | §5.1 硬件与组件分配 | `HISTORICAL_DESIGN`（GPU 分层与 Rerank Batch Aggregator 的 CPU→GPU 迁移叙述） | 见 §4.2：当前是共享 4B 端点 |
> | §5.2 KV Cache 建模与准入 | `CURRENT`（`admission/kv_admission.py` 已接入 `core/pipeline.py`）+ `DESIGN_TARGET`（所有 KV 数值、并发区间、安全系数） | 双因子 KV 成本模型已实现；`kv_per_token≈0.45 KB` 是模型估算，从未实测 |
> | §5.2.5 KV 压力 Spill/Reject | `CURRENT`（分级降级链已实现）+ `HISTORICAL_DESIGN`（本节阈值区间，见上方 reconciliation） | 以代码常量为准 |
> | §5.2.6 吞吐模型统一表达 | `DESIGN_TARGET` | 推导闭环，无实测 |
> | §5.3 vLLM continuous batching 认知 | `CURRENT`（设计原则正确且被代码遵守） | — |
> | §6 多模态能力 | `CURRENT`（MinIO 60s 签名 URL + 端点内权限重校验已实现）+ `HISTORICAL_DESIGN`（BLIP 触发率与双路触发表述） | 见上方 reconciliation |
> | §7.1 并行多路召回 | `CURRENT` | 4 条路径、权限/版本下推、ES Fallback、去重归一化均已实现 |
> | §7.2 检索一致性评分 | `CURRENT`（`retrieval/parallel_recall.py::_compute_agreement_score`，MiniBatchKMeans 熵 + Jaccard，0.7/0.3 混合，已接入 Evidence Gate）+ `DESIGN_TARGET`（阈值调优、告警、每月实体 Recall@K/F1 评估） | **注意**：聚类向量是 `[score, hash(doc_id)%1000/1000]` 代理量，不是真实文档向量；结论只能说明各召回源是否指向同一簇，不能说明语义质量 |
> | §7.3 两阶段 Rerank | `CURRENT`（BiEncoder 150 → 双 CE 精排 10、Platt 校准、请求内批量）+ `DESIGN_TARGET`（跨请求微批聚合、GPU P99 ≤60ms） | `batch_predict()` 是**请求内**同步批处理；跨请求 `submit_batch()` 未接入主链路。§5.2 的延迟改善数字未实测 |
> | §7.4 Evidence Ensemble Gate | `CURRENT`（四因子加权 + 三档决策 + 法规矛盾拒答） | 权重 w1~w4 来自 `config.json`，**未实现**离线日志学习 |
> | §8 Fail-safe | `CURRENT` | 业务逻辑返回 200 + 结构化拒答；仅基础设施故障 / 极端过载返回 503 |
> | §9 弹性降级与限流 | `CURRENT`（令牌桶 + KV 分级降级 + Prefix Caching 保护）+ `HISTORICAL_DESIGN`（本节阈值区间） | 见上方 reconciliation |
> | §10.1–§10.3 缓存 Key 与分层 | `CURRENT` | 权限 + 版本进入 Key 空间；L1 只服务 public、L2 物理分区 |
> | §10.4 Redis 职责 | `CURRENT` | L2 命中即视为权限通过；Redis 只做 TTL 与 Key |
> | §10.5 失效策略 | `CURRENT`（epoch 切换即自然失效）+ `HISTORICAL_DESIGN`（Airflow 自动 bump） | 激活为人工动作 |
> | §10.6 语义安全缓存 | `CURRENT` | `requires_context=true` 仅同 session 复用 |
> | §10.7 CLIP 缓存策略（已移除） | `CURRENT` | 已删除 query-hash CLIP embedding cache |
> | §11 权限与安全审计 | `CURRENT` | 掩码/过滤表达式/拦截原因入审计；query SHA256 脱敏 |
> | §12 可观测性 | `CURRENT`（追踪钩子、OTLP exporter 默认关闭、`/api/metrics`、6 条告警、10 面板）+ `PENDING_VALIDATION`（运行期闭环、生产触发、面板填充）+ `HISTORICAL_DESIGN`（Jaeger thrift agent 路径，已移除） | 本节列出的 KV / GPU / batch 指标**多数不存在**；真实存在的是 `rag_*` 原始计数器，边界见 evidence map |
> | §12.1 KV Cache 分类与监控 | `HISTORICAL_DESIGN` | Shared / Per-request 分类未实现为独立监控项；`kv_pressure` 经 `/api/stats` 暴露但无告警 |
> | §12.2 离线反馈闭环 | `CURRENT`（A/B 平台 `common/ab_testing.py` 已接入 `core/pipeline.py`；反馈 review 门控已实现）+ `DESIGN_TARGET`（每周采样 500 条人工标注、2000 条标注样本集、每周贝叶斯优化 RRF 权重、ROC 调阈值、1 周 A/B 后全量发布） | **没有任何一条自动学习闭环在运行**；A/B 代码路径存在但未配置任何实验 |
> | §13 API 服务拆分 | `HISTORICAL_DESIGN` | `/rewrite`、`/generate` 及其 P99 目标是拆分设计；当前主线是单体 |
>
> **未验证的数字一律是数字，不是结论。** 本文出现的所有延迟、QPS、并发、KV 与 GPU 数值都是
> `DESIGN_TARGET`。仓库内**没有任何**可复现的 benchmark artifact，因此任何章节都不得被引用为
> 「系统达到 X」。
>
> **运维契约（v2.5 working milestone，已实现）**：`GET /api/stats` 与 `GET /api/metrics` 需要身份认证（`require_identity`）；Docker Compose 的 Elasticsearch 启用 `xpack.security.enabled=true` 并要求 `ELASTICSEARCH_USERNAME`/`ELASTICSEARCH_PASSWORD`；登录限流仅在配置 `TRUSTED_PROXIES` 时才信任 `X-Forwarded-For`。操作细节见 [docs/deployment-guide.md](docs/deployment-guide.md) 与 [docs/operations-guide.md](docs/operations-guide.md)。
>
> **本地真实验证（v2.5 working milestone，`LOCAL_REAL_VALIDATION`）**：真实 Redis 多进程会话持久化/跨进程限流、真实 nginx / `TRUSTED_PROXIES`、认证 Elasticsearch、认证 Prometheus 抓取已在本地真实依赖上验证，证据见 [docs/validation/v2.5-runtime-security-validation.md](docs/validation/v2.5-runtime-security-validation.md)。这是本地验证，不是生产集群 / HA / SLO 验证。

> **Historical Production Context（`HISTORICAL_PRODUCTION`）**：以下为作者此前公司生产环境的业务规模与流量背景，用于解释设计动机。公开仓库**不包含**对应的专有语料、生产日志、模型权重或监控数据，因此这些数字不是 `REPO_VERIFIED`，也不是可复现 benchmark：
> 3000+ 文档、5000+ 图片、1500+ 产品、2000+ 成分、8 大法规体系、200+ 内部用户；高峰短时 10–15 QPS，日均 1500+ 请求。
> 生产观测（production observation）与仓库可复现基准（repository reproducible benchmark）不得互相替代；未经仓库内 benchmark artifact 证明，不得把上述规模或性能当作当前仓库已验证能力。

1. 项目背景
面向中小型化妆品企业（研发/品质/法规/销售），构建统一知识问答系统，解决成分/法规/配方知识分散、法规体系复杂（8大体系）、非结构化数据占比高、传统知识库不支持多轮与跨模态查询等问题。
2. 系统目标与指标
● 构建统一知识中枢，支持多模态问答，低幻觉高可信，细粒度权限控制（RBAC）。
● 性能指标（系统吞吐模型约束）：
  ○ 延迟：1.5–3.0s（P95≈2s，P99≤3s），分阶段拆解详见下表。
  ○ 有效并发：由 KV Budget 与 token 分布动态决定，典型混合 workload 下有效并发为 20–25（详见第 5 节并发控制模型）。
  ○ QPS：12–18（混合流量推导值，由并发与延迟数学关系约束）。
  ○ 最大输出 tokens：动态（256–1024，按业务类型）。
  ○ 多轮对话保留：最近 6 轮原始对话（FIFO）。
2.1 端到端延迟拆解（P95 参考）
阶段	P95 延迟
Rewrite	40–60ms
Embedding + 路由	50–100ms
检索（Qdrant+ES 并行）	80–150ms
CrossEncoder Ensemble（GPU Batch）	30–60ms
NLI / Gate（GPU Batch）	10–25ms
LLM Decode（含 Prefill）	900–2000ms
合计（全链路 RAG）	1.1s – 2.3s
P99 波动（长尾 Decode）	2.5s – 3.2s
设计说明（未实测）：目标架构拟将 CrossEncoder 与 NLI 从 CPU 串行处理调整为 GPU 批处理，并支持跨请求 batch 聚合。此处延迟区间和吞吐改善均为未验证目标；仓库没有可复现 benchmark artifact，不能据此声称生产性能提升。
2.2 吞吐模型（Capacity Model）
系统性能由以下核心关系约束：
QPS ≈ 有效并发 / 平均延迟
有效并发 = KV_Budget / E[KV_per_active_sequence(t)] × safety_factor
● KV_Budget：GPU0 为 14B 模型预留的 KV Cache 总量（≤8GB）× 安全系数 0.7。
● E[KV_per_active_sequence]：基于请求序列长度分布（长尾分布）计算的 KV 成本期望值，非固定均值常数。
● QPS 模型示例：若假设 Avg latency=1.6s、有效并发=25，则数学推导 QPS≈15.6；这是未经 workload benchmark 验证的估算，不是生产结果。
注：QPS 为推导值而非固定配置，实际承载能力随 workload mix、序列长度分布、KV 动态占用而变化。轻量 rewrite 的 25–60 QPS 也是设计估值，未经可复现实测。
3. 离线知识库构建
3.1 数据范围（设计阶段 baseline）
以下为早期设计阶段的规模基线，用于约束架构选型，**不是当前仓库可复现的生产规模**：
500+ 文档（PDF/Word/Excel）、包装图片/扫描件，覆盖 2000+ 成分、3000+ 配方（原料研发配方种类）、1500+ 产品（实际制造产品）、8 大法规体系。
历史生产环境的真实规模与流量上下文见文档顶部 **Historical Production Context（`HISTORICAL_PRODUCTION`）**；两者不得互相替换。
3.2 文档处理
**实现状态：** 当前实现按**字符**切块（默认 500 字符、10% 重叠），不是 tokenizer 切块；设计中的
JSON Lines 中间产物未采用，解析结果以有序 block（含 page/heading/paragraph/table/row_window
元数据）直接进入 chunker。
● 文本清洗去噪 → 有序结构化 block → 字符切块（≈500 字符，重叠 10%）。
● 向量化：bge-base-zh-v1.5 生成 768 维向量。
● 元数据记录：doc_type、page_number、heading_level、sheet_name、row_start/row_end 等。
● 权限字段注入（Bitmask）：离线按 `permission_rules` 计算 role_mask 和 dept_mask。
3.3 图像处理
**实现状态：** OCR provider 与 CLIP 图像 encoder 已实现并解耦（可注入）；PaddleOCR 为可选外部
运行时，默认不安装。视觉权重规则已实现且确定性可测。**注意：** 当前 OCR 文本通过文本 writer
写入，`embedding_type` 记录为 `"bge"`，尚未单独标记为设计中的 `"image_ocr"` 类型。
● 图像增强（二值化、去噪、倾斜校正）→ PaddleOCR 中文模型输出带坐标/字号/置信度的文本块。
● 视觉权重注入：对核心区域（居中、大字号、高置信度）文本按权重重复（如 3 次），拼接为
  ocr_main_text 用于向量化；保留完整 ocr_full_text 用于溯源。
● BGE 向量化 OCR 文本（当前 embedding_type = "bge"）。
● CLIP-ViT-B/16 离线生成 512 维图像向量，embedding_type = "image_clip"。
● BLIP 不在离线阶段执行，仅在线按需触发。
3.4 向量存储与权限标签（Bitmask）
采用多 Collection 物理隔离解决维度差异：
● rag_text_768：存储文档切块与 OCR 文本向量（768d）。
● rag_image_512：存储图片 CLIP 向量（512d），含 image_uri。
权限字段优化：使用 uint32 位图替代数组，单条判断 O(1)，过滤复杂度 O(K)（K 为 ANN 候选数）。RBAC 掩码契约以 uint32 为准（`0` 表示公开/无限制；`0xFFFFFFFF` 为 super_admin 绕过；配置的 `admin` 掩码同样绕过）。Qdrant 使用 Cosine 距离；仓库当前未声明任何 IVF / ANN 调优参数，检索行为以实际 collection 与 writer 为准。
3.5 Bitmask 编码规则与访问判定
● 掩码契约：所有 role_mask / dept_mask 均为 uint32（0 ≤ mask ≤ 0xFFFFFFFF）。
● 0x00000000：公开/无限制；role 与 dept 同时为 0 时为全公开文档，任何用户可访问。
● 配置的 admin 掩码与 super_admin_mask（0xFFFFFFFF）都绕过所有检查。
● 普通 RBAC：role 位与 dept 位均需匹配（doc_dept_mask=0 表示不限制部门）。
● 缺失、类型错误、负数或超过 uint32 的权限元数据必须 fail closed（拒绝），不得当作公开。
统一访问判断规则（应用层，实现于 common/auth.py）：
def is_allowed(doc_role_mask, user_role_mask, doc_dept_mask, user_dept_mask):
    if is_admin_role_mask(user_role_mask):  # 配置的 admin 或 super_admin_mask
        return True
    if doc_role_mask == 0:
        if doc_dept_mask == 0:
            return True
        return (doc_dept_mask & user_dept_mask) != 0
    role_ok = (doc_role_mask & user_role_mask) != 0
    dept_ok = (doc_dept_mask == 0) or ((doc_dept_mask & user_dept_mask) != 0)
    return role_ok and dept_ok
Qdrant/ES 过滤表达式（下推）：
((role_mask == 0) OR ((role_mask & {user_role_mask}) != 0)) 
AND (dept_mask == 0 OR ((dept_mask & {user_dept_mask}) != 0))
AND doc_version_epoch == {active_epoch}
AND status == 'active'
注意：Qdrant 标量过滤不支持直接位运算，RBAC 权限过滤需在 Python 应用层 post-filter 完成。ES 支持 Script 位运算下推。
3.6 文档生命周期与版本化管理（核心修订）
引入基于 doc_version_epoch 的版本化管控替代实时时间判断，消除因 expiry_date 变更引发的缓存全量失效问题：
● 元数据扩展：effective_epoch（生效版本）、expiry_epoch（过期版本）、status（active/archived）。
● 实现状态：epoch 由 CLI/调度器构建并封存；**不会自动切换生产 active_epoch**。`knowledge_version_epoch` 的切换是明确的人工发布动作。设计中“Airflow 自动 bump”未实现。
● 检索过滤逻辑：所有检索（Qdrant/ES）均使用 doc_version_epoch == {active_epoch} 作为硬性约束，不再依赖 expiry_date > current_timestamp() 运行时判断。
● 设计目标：定时任务将 expiry_epoch < active_epoch 的文档标记为 archived，并更新状态；前端历史区间查询尚需按实际代码验证。
3.7 离线调度
实现状态：业务逻辑位于 `offline/scheduler.py`（cron/Airflow/CLI 共用），频率来自
`config.json` 的 `offline.scheduler`。`dags/knowledge_base_dags.py` 在 Airflow 可用且离线模块
可发现时注册增量/全量/反馈 DAG。默认 Compose 不启动 Airflow；真实 Airflow 执行未验证。调度器
最多 build/validate/seal，不自动激活 epoch。
4. 在线推理架构
4.1 核心链路
Query → 用户身份解析 → 二级缓存（L1/L2）
● L1 HIT：直接返回（仅服务全公开文档，无需二次校验）
● L2 HIT：缓存 Key 已包含权限掩码，命中即表示权限通过，直接返回
● MISS：复杂度评估 → Query Rewrite（输出 business_type/intent）→ 权限前置绑定 → 双 Embedding 路由（BGE + CLIP Text）→ 并行多路召回（权限与版本下推） → Union 合并与去冗余 → BiEncoder（宽保留）→ Rerank Batch Aggregator（GPU 微批聚合） → CrossEncoder Ensemble → Evidence Ensemble Gate（投票机制） → LLM 生成 → Answer Gate（NLI 校验）
4.2 执行层隔离（当前实现：单一共享 4B 端点；以下双实例为历史/目标设计）
【当前实现】仓库使用**单一共享 4B vLLM 端点**：`config.json` 的 `gpu1.models.vllm_4b`（端口 8101）同时服务 Query Rewrite 与简单生成（endpoint 键 `gen_4b`），复杂生成路由到 `gpu0.models.gen_14b`（Qwen3-14B，端口 8100）。路由实现见 `router/stateless_router.py`。Compose 覆盖层服务名为 `vllm-4b` / `vllm-gen-14b`。
【历史/目标设计】以下双 vLLM 实例描述为原始目标设计，**不代表当前实现**：
设计原则：遵循 vLLM 原生 continuous batching 机制，不在外部实现任何请求队列或优先级抢占逻辑。系统仅做无状态路由分发，所有并发调度完全交由 vLLM 内部 Scheduler 处理。
● vLLM-Rewrite：Qwen3-4B，max_tokens=192，专用 KV Cache。（历史设计）
● vLLM-Gen-4B：Qwen3-4B，KV Cache 独立。（历史设计）
路由规则（Stateless Dispatcher，历史设计）：
● Rewrite 请求直接转发至 vLLM-Rewrite 实例（历史设计），不经过队列排序，不抢占 Gen 资源。
● Gen 请求直接转发至 vLLM-Gen-4B 或后续 14B 模型实例（历史设计）。
● 删除项：原 CPU Orchestrator 中的优先级队列、Redis 排队状态管理、Rewrite 超时降级调度逻辑。
● 关键修正（历史设计）：Rewrite 定位为轻量预处理阶段，若 vLLM-Rewrite 实例繁忙（由 Admission Control 判断 KV 压力），则直接在应用层触发结构兜底（降级为规则解析），绝不进入阻塞式等待队列，避免破坏 vLLM 的批次合并效率。
4.3 模型分级路由
● BERT 复杂度评估（0.3B，二分类）：简单问题 → 共享 4B 端点（`gen_4b`）；复杂问题 → Qwen3-14B (4-bit NF4)（`gen_14b`）。
  状态：仓库包含 QLoRA 训练脚本、样本数据和 PEFT AdapterManager 代码；本 PRD 不据此声称训练已验证。训练产物需单独生成，adapter 加载还依赖运行配置、依赖和模型资产。
4.4 Query Rewrite（路由增强器，非核心强依赖）
定位修正：Rewrite 作为“路由增强器”而非必经中心节点，主链路已具备独立检索与生成能力。
● 输入：原始 query + 最近 6 轮对话。
● 输出强制 JSON Schema（含 rewritten_query, business_type, intent, requires_context, standardized_entities）。
● 校验与兜底：JSON parse 失败重试一次，二次失败则降级（business_type="general", confidence=0.3, fallback=True）。
● 逻辑一致性修正：若 business_type="regulation" 且 intent 为图像类，强制修正 intent="compliance"。
失败降级策略（消除单点依赖）：
Rewrite 失败时绝不返回 503，而是进入“结构兜底 + 保守执行”：
1. JSON 解析失败：重试 1 次（temperature=0），仍失败则进入结构兜底。
2. 结构兜底：利用规则/关键词 + BERT 意图分类生成基础字段（business_type="regulation", intent="compliance", requires_context=True），rewritten_query 回退为原始 query。
3. 保守执行策略：识别为法规兜底场景时，强制提升检索量（TopK 100→300）、强制开启 RAG、禁止缓存命中、输出长度收缩、强制开启 Evidence Gate 且阈值提升至 ≥0.8。
4. 最终交互兜底：若 Evidence Gate 未通过，返回“无法确认，请补充信息”的引导语（HTTP 200），而非 503 错误。
数据闭环校准：*设计目标*——Rewrite 输出字段（intent/business_type）的准确性通过离线人工标注样本（gold set）**每周**评估，并基于评估结果自动更新 prompt 模板与 BERT 分类器阈值。
> ★ 该评估与自动更新**未实现，也无调度**。仓库内的评测集是 `tests/evaluation/golden_set.jsonl`（301 条，
> 用于 RAGAS 格式校验），不是用于路由阈值调优的人工标注 gold set。
4.5 双 Embedding 路由与 CLIP 同步策略（修正版）
设计原则：
1. CLIP 不允许纯异步脱离主链路：多模态召回必须参与主决策。
2. 异步仅用于增强：补充召回与多轮预热，不替代主召回。
3. 是否同步由检索价值决定：而非单一的 Intent 标签。
1) CLIP 参与策略：轻量多模态判别器
引入轻量级判别逻辑决定 CLIP 是否同步参与主链路：
is_visual_relevant = max(
    keyword_rule_score,           # 关键词规则（如“图片”，“包装”，“外观”）
    bert_classifier_score,        # 微调 BERT 分类器
    query_emb vs image_centroid_sim # Query 向量与图像库质心的相似度
)
决策路由表：
条件	行为
≥ 0.6	✅ CLIP 同步参与主链路（确保首轮图像召回）
0.3 – 0.6	⚠️ CLIP 低成本同步（仅检索 TopK=20，控制延迟）
< 0.3	❌ 跳过 CLIP（纯文本语义检索）
2) 同步 CLIP 执行（受控延迟）
● 【已实现】CLIP 作为可注入的 embedder 适配器，超时/异常时丢弃该分支并仅依赖 BGE 文本检索。
● ★ 「CPU ONNX Runtime + SIMD 加速」「正常耗时 ≤80ms」「超时阈值 120ms」——**未实现或未实测**：
  代码中没有 ONNX Runtime 执行路径，也没有这两个时间常量。
3) 异步机制重定义（非阻塞增强）
异步流程仅在主链路返回后执行，用于以下三个场景：
● (1) 补充召回：主链路返回后，CLIP 后台检索 TopK=100，结果写入当前 Session 的临时缓存，用于后续可能的追问。
● (2) 多轮对话预热：当用户进行下一轮 follow-up 提问时，优先复用上一轮异步检索到的图像结果，实现真正的“预热命中”。
● (3) 离线权重统计：记录 CLIP 结果在 Rerank 阶段的贡献度，用于离线调整 RRF 融合权重，不用于构建 Query Embedding Cache。
4) 删除 CLIP Embedding Cache（关键修正）
● 移除项：Redis-Cache 中基于 hash(query) 存储 CLIP Text Embedding 的逻辑。
● 删除原因：
  ○ Query 受 Rewrite 与多轮对话影响，语义漂移大，Cache 命中率趋近于 0。
  ○ Embedding 对改写极其敏感，无法复用。
  ○ 避免内存浪费与虚假的缓存监控指标。
5) 多模态融合权重（当前为查询类型静态映射，非学习所得）
加权 RRF 融合公式：
final_score = w_text * text_score + w_clip * clip_score + w_ocr * ocr_score
● 【已实现】权重按 business_type / is_visual_relevant 做确定性映射，数值来自 `config.json`。
● ★ 设计目标：权重由历史日志（rerank 点击率与人工标注相关性）分析得出，并**每周**根据 A/B 实验更新；
  w_clip 的 2.0 / 1.0 / 0.0 来自线下网格搜索与线上验证。**学习与调参环节均未实现**，见 §12.2。
● 因此本系统**不是**「权重随检索价值自学习」的系统；它是「权重由配置驱动、随查询类型切换」的系统。
4.6 长文本生成一致性保障
【状态：`CURRENT`（已实现）——但实现方式与本节原始设计不同，见下】
**实现事实**：首段生成被截断后走 `core/pipeline.py::_try_structured_continuation()` →
`models/llm_client.py::generate_continuation()`。该函数把已生成内容作为 **assistant 消息回放进
prompt**（`_escape_reserved_trust_boundary_markers()` 做信任边界转义），再追加一条约束指令
（保持语义/语气/结构一致、不重复已输出、仅补充后续部分、引用来源必须与前文一致），
以 `temperature=0` 重新请求，最后由调用方 **拼接** 两段：

```python
merged_answer = ctx.generation_result.answer + continuation.answer
```

> **与原始设计的差异**：本节原写「不采用基于上次输出尾部续写，采用上下文重建 + 单次重生成」。
> 实际实现是 **前缀回放 + 指令续写 + 结果拼接**，即：它确实把上次输出放回上下文要求模型继续，
> 只是在语义上要求「只补后续、不重复」。把它读成「一次完整推理的拆分展示」是不准确的。
> 「可选 logit_bias / 确定性解码锁定前半部分」中的 `temperature=0` 已实现，`logit_bias` 未实现。
● 触发条件：当生成内容超过业务类型对应的最大输出 tokens（如法规 1024），返回 session_id 和 has_more=true，但不暴露中间生成结果作为续写输入。
● 续写请求处理流程：
  a. 重建完整上下文。
  b. 构造约束式 Prompt，明确要求模型：
     ○ 保持与已有回答的语义、语气、结构“完全一致”
     ○ 不重复已输出的内容
     ○ 仅补充后续部分
  c. 重新调用 LLM 进行完整生成：
     ○ 包含原始 query、Rewrite 结果、首次检索锁定的 Top-K 证据 doc_id 列表（证据锁定）、已生成部分 answer（作为“约束前缀”，而非续写 prompt 主体）。
     ○ 可选使用 logit_bias 或确定性解码（temperature=0）锁定前半部分输出，确保前缀字符级一致。
● Answer Plan 结构化增强（法规类强制启用）：
首次生成时要求模型输出结构化大纲：
{
  "outline": ["成分安全评估", "法规限制条款", "使用建议"],
  "answer": "..."
}
续写时根据 outline 定位当前已完成的章节，模型仅补充剩余章节内容，从根本上杜绝重复与结构紊乱。
● 证据锁定（Evidence Locking）：
首次生成后，将参与 Rerank Top-3 的 doc_id 列表持久化至 session 上下文。续写请求禁止重新检索（或仅允许在原有 Top-K 基础上补充极少新文档，且不允许替换核心证据），确保前后结论依据一致，避免法规类场景出现“证据漂移”导致的逻辑矛盾。
● KV Cache 使用原则：
vLLM 的 Prefix Caching 仅用于加速相同前缀的生成请求，不能跨请求保证推理一致性。本方案通过 Prompt 重建保证一致性，而非依赖 KV Cache 续写。
4.7 动态输出长度控制
● 按业务类型分级：法规 1024、研发 768、通用 512、简短 256。
● KV Cache 降级联动：>90% 时采用应用层流式截断，不修改 vLLM 的 max_tokens 参数以维持 Prefix Caching 有效性。
5. 双卡资源部署与并发准入控制（核心修订）
5.1 硬件与组件分配
层级	硬件	组件	显存/资源
生成推理层	GPU0	Qwen3-14B (4-bit NF4, vLLM)	~8GB 模型 + ≤8GB KV Cache
控制与轻推理层	GPU1	共享 4B vLLM 端点 (`vllm_4b`，rewrite + 简单生成)、BERT (0.3B)、CLIP Image Encoder、Rerank Batch Service（CrossEncoder/NLI/BiEncoder/CLIP Text/BLIP）	~7.0GB + Rerank 动态占用
重排序与批处理层	GPU1 (原 CPU 集群迁移)	CrossEncoder Ensemble、BiEncoder、CLIP Text Encoder、NLI、BLIP（GPU Batch + 微批聚合器）	统一 GPU1 调度，CPU 仅负责轻量逻辑
GPU1 调度（无状态路由 + GPU 批处理模式）：
● 架构原则：去除外部 CPU Orchestrator 调度排队。各模型作为独立 vLLM 实例或 GPU Batch Service 运行，系统上层仅包含一个无状态 Router。
● Router 职责：根据请求类型（Rewrite / Gen / Rerank）直接将请求转发至对应的 vLLM 引擎端口或 GPU Batch 服务，不进行请求排队、不维护优先级队列、不干预 vLLM 内部 continuous batching 决策。
● Rerank Batch Aggregator：
  ○ 【已实现】`retrieval/rerank_batch_aggregator.py`，聚合**请求内**的 (query, doc) pair；
    被 `cross_encoder_ensemble.rerank()`（CrossEncoder 双模型）与 `answer_gate`（NLI 批量）调用。
  ○ ★ time-based 窗口（10–20ms）、跨请求聚合、BLIP 异步队列批处理、独立 CUDA Stream——
    **均未实现**。跨请求路径需显式调用 `submit_batch()`，主链路不调用。
5.2 KV Cache 资源精细化估算与 Admission Control（系统级并发准入）
5.2.1 设计原则
系统引入 KV-aware admission control，将并发从“请求级并发控制”升级为 “token-level KV budget scheduling”，确保在 vLLM continuous batching 下 GPU KV cache 使用率稳定在安全阈值以内，避免 OOM 风险。核心修正点：
● ❌ 废弃静态并发数“35”的设定
● ✅ 并发由 KV Budget、请求序列长度分布、实时 KV 压力三者动态决定
5.2.2 KV Cache 本质建模（替代“MB/token 常数模型”）
KV Cache 基于模型真实结构进行建模，而非单一常数：
KV_per_token(layer) = 2 × layers × hidden_size × bytes_per_param × batch_factor
对于 Qwen3-14B（GQA 结构）：
● layers = 40（具体依变体而定）
● hidden_size 经 GQA 压缩（KV heads 减少）后每 token 实际开销约为传统 MHA 的 1/4 ~ 1/8
● 模型估算 kv_per_token ≈ 0.45 KB/token（即 0.45 MB / 1000 tokens）；未在本仓库实测，见 §2.2 与性能产物状态 `PENDING`
关键认知：KV Cache 是随 token 序列 + batch 动态增长的三维张量资源，而非固定 MB/token 常数。其增长遵循 O(seq_len × batch_size)，受 continuous batching 动态影响。
5.2.3 KV Budget 准入控制模型
系统采用 Token-Based Concurrency Control，不再使用固定并发数，而是基于实时 KV 预算动态限制在途请求数量。
系统 KV Budget 计算：
KV_Budget = GPU_KV_Cache_Total × safety_factor (0.7)
● GPU_KV_Cache_Total：GPU0 为 vLLM 预留的 KV Cache 总量（本设计 ≤8GB）。
● safety_factor：保守系数 0.7，为连续批处理峰值与 Prefill 重叠留出缓冲。
单请求 KV 成本估算（Token-level forecast model）：
KV_cost(request) = input_tokens × prefill_kv_factor + expected_output_tokens × decode_kv_factor
其中：
● prefill_kv_factor ≠ decode_kv_factor（decode 阶段 KV 持续增长，成本更高）
● expected_output_tokens 由业务类型预设（法规 1024、研发 768 等）
示例：典型法规问答请求 input=800, output=1024 → 1824 tokens → KV Cost ≈ 1824 × 0.45 KB ≈ 0.82 MB
最大可承载并发计算（分布期望模型）：
Max_Effective_Concurrency = floor(KV_Budget / E[KV_per_active_sequence(t)])
其中：
E[KV_per_active_sequence] = Σ P(length_i) × KV(length_i)
● P(length_i) 为实际请求序列长度分布（长尾分布），非拍平均值。
● 该模型从“平均值模型”升级为“分布期望模型”，更准确反映真实负载。
业务类型 KV 成本参考表：
业务类型	典型 input+output tokens	单请求 KV 成本	有效并发估算（8GB × 0.7 / cost）
法规问答	1824	~0.82 MB	~39
通用问答	912	~0.41 MB	~78
Rewrite	256	~0.12 MB	~266
实际并发控制策略：
系统不对外暴露固定并发数，而是通过以下三层机制动态控制：
1. vLLM 层硬限制：
  ○ max_num_seqs = 动态计算（由 Admission Control 根据实时 KV 压力调整，非写死值）
  ○ max_model_len = 4096 / 8192（视模型）
  ○ gpu_memory_utilization = 0.85（保守）
2. Token-Level Admission Limiter：
  ○ 在请求进入 vLLM 引擎前，预估其 KV 成本，若 当前已占用 KV + 预估成本 > KV_Budget × 0.85，则请求进入排队或触发降级。
  ○ 排队优先级：P0（法规）> P1（研发）> P2（通用/闲聊）。
3. 动态并发范围（参考值，非固定配置）：
  ○ 常态有效并发：20–25（稳定运行区间）
  ○ 峰值有效并发：≤30（短时可承受）
  ○ 安全阈值：KV 利用率 ≤80%
5.2.4 多实例 vLLM KV 资源模型（关键修正）
❌ 删除错误认知：原“GPU1 多 vLLM 实例 KV 独立”表述。
✅ 正确模型：
GPU1 KV资源模型 = Unified KV Block Pool

多个 vLLM 实例 = 逻辑隔离，不是物理隔离

KV影响：
- KV pool 被 partition
- block reuse efficiency ↓
- fragmentation risk ↑
多实例 ≠ KV 独立，只是 scheduler namespace 隔离。vLLM 的 KV 是 runtime block pool，多实例导致 pool 分割与 fragmentation 风险，不是简单的“叠加/独立”。
vLLM 的 continuous batching 使 KV Cache 不是按请求分配，而是按 token block 动态调度，因此系统瓶颈由 KV + scheduler + fragmentation 三者共同决定，而非单一显存预算。
5.2.5 KV 压力监控与 Spill/Reject 机制
新增 KV Pressure Monitor，实时计算：
KV_Pressure = current_used_kv / max_kv_capacity
触发策略：
KV Pressure	行为	代码常量
（更早）	令牌桶收紧，请求排队	`threshold_tighten`
> 0.80	应用层流式截断（**不改** `max_tokens`），P0 法规收缩输出	`THRESHOLD_TRUNCATE = 0.80`
> 0.90	soft stop：P1 降级至 4B、P2 丢弃	`THRESHOLD_SOFT_STOP = 0.90`
> 0.95	极端过载：按优先级分别拒收 / 排队 / 降级	`THRESHOLD_CRITICAL = 0.95`

> **已按实现更正**：本节最初写的是 0.7 / 0.8 / 0.9 / 0.95。`admission/kv_admission.py` 实际的
> `THRESHOLD_TRUNCATE` 是 **0.80**、`THRESHOLD_SOFT_STOP` 是 **0.90**、`THRESHOLD_CRITICAL` 是
> **0.95**，且降级顺序为「截断 → 降级 → 拒收」，与上表一致。这些数字仍是 `DESIGN_TARGET`：
> 它们是配置默认值，从未在真实 GPU 负载下调优过。
当 KV 预算不足时的优先级降级顺序：
1. reject low priority request (P2)
2. truncate output tokens
3. downgrade model (14B → 4B)
4. fallback to retrieval-only answer
5.2.6 吞吐模型的统一数学表达（核心闭环）
系统并发、延迟、QPS 三者通过以下关系严格绑定：
QPS ≈ 有效并发 / 平均延迟
有效并发 = KV_Budget / E[KV_per_active_sequence(t)] × safety_factor
代入典型混合 workload（法规:研发:通用 ≈ 0.4:0.3:0.3，E[KV_per_active_sequence] ≈ 0.6 MB）：
● KV_Budget = 8GB × 0.7 = 5.6 GB
● 有效并发 ≈ 5.6 GB / 0.6 MB ≈ 30（理论最大）
● 考虑 vLLM 调度开销与安全边界，常态取 20–25。
● 平均延迟 = 1.6s → QPS ≈ 25 / 1.6 ≈ 15.6（区间 12–18）。
该模型已消除原指标中“并发 25–35”与“QPS 10–20”之间的不自洽问题，并明确了 QPS 是推导值而非固定配置。
5.3 vLLM 正确认知补充（必须加）
vLLM 的 continuous batching 使 KV Cache 不是按请求分配，而是按 token block 动态调度，因此系统瓶颈由 KV + scheduler + fragmentation 三者共同决定，而非单一显存预算。PagedAttention 通过 block 管理减少 fragmentation，但多实例部署会降低 block reuse efficiency，需在架构设计阶段纳入考量。
6. 多模态能力
● 支持范围：仅限离线已向量化图像，不支持用户实时上传图片在线解析。
● 默认路径：CLIP 向量（视觉相似）+ OCR 文本向量（语义）。
● 按需 BLIP：**三路**决策取最大触发——关键词规则 + BERT 意图分类 + 是否命中图像结果（`models/blip_service.py`：`max(keyword_rule_score, bert_classifier_score, has_image_hit)`，阈值取 `gpu1.models.blip.trigger_threshold`，默认 0.3）；结果缓存 TTL 1h。GPU 批处理延迟 ≤120ms 是**设计目标**。
  > 本节原写“**双路**决策触发，触发率 <5%”。代码是三路，模块文档记录的触发率是 **~15%**；两者都与本文不符，已按实现更正。触发率本身是设计估计，不是实测分布。
● 加权 RRF 融合：权重由 is_visual_relevant 和 business_type 动态决定（视觉相关度高时 CLIP 权重提升，法规查询提高 BM25 权重），具体数值取自 `config.json`。★ 「通过离线点击日志与相关性标注学习并定期更新」**未实现**，见 §12.2。
● 资源访问安全：检索返回 doc_id，前端请求 /api/media/{doc_id} 获取 MinIO 临时签名 URL（60s 有效期），端点内执行权限重校验。
7. 检索与排序（重构：鲁棒多路召回与证据投票机制 + GPU 批处理化）
7.1 并行多路召回与权限过滤（消除三段式递归）
设计理念变更：
● ❌ 删除“三段式递归 TopK（100→300→500）”补召回逻辑。
● ✅ 替换为“并行多路召回 + 冗余覆盖”，消除延迟翻倍问题，确保召回稳定性。
并行执行通道（必须同时执行）：
1. Dense 语义路：BGE (Qdrant text_768 & ocr_768)
2. 关键词精确路：BM25 (ES)
3. 视觉语义路：CLIP (Qdrant image_512，受 4.5 节判别路由控制)
4. 改写泛化路：基于 Query Rewrite 生成的 2~3 个变体 Query 进行 Dense 检索
权限与版本过滤下推：
所有检索请求均携带用户 role_mask、dept_mask 及当前 active_epoch，利用 Qdrant/ES 标量索引完成过滤，延迟 1–2ms。Python 层不再执行任何运行时权限过滤（注：Qdrant 标量过滤不支持位运算，RBAC post-filter 仍在 Python 层，但仅对 top-K 候选执行，非全量）。
ES Fallback 与稀疏权限兜底：
● 当 Qdrant 不可用或召回有效文档数 < 50 时，自动切换 ES 全文检索。
● ES 查询直接应用相同的位运算过滤条件。若 ES 版本不支持位运算脚本，则采用预计算的 role_bucket 字段进行 terms 过滤，避免 Python 层 GIL 循环开销。
输出融合：
Union Recall Set = R_bge ∪ R_bm25 ∪ R_clip ∪ R_rewrite
对 Union Set 进行简单的 doc_id 去重与分数归一化，统一进入 Rerank 层。
7.2 检索一致性评分（解决 Recall 不稳定核心）
【状态：`CURRENT`（已实现并接入 Evidence Gate）+ `DESIGN_TARGET`（阈值调优与告警未实现）】
关键指标 Retrieval Agreement Score 已实现：`retrieval/parallel_recall.py::_compute_agreement_score` 用 MiniBatchKMeans 聚类熵（权重 0.7）与各路 Top-K 的 Jaccard 重合度（权重 0.3）混合，并由 `core/pipeline.py` 传入 Evidence Gate 作为 w3 因子。
> **边界**：聚类使用的是 `[score, hash(doc_id)%1000/1000]` **代理向量**，不是真实文档向量。因此它只能说明「各召回源是否指向同一簇」，**不能**说明簇内语义质量。
● 计算逻辑：对召回集合中的文档向量进行聚类（如 MiniBatchKMeans），计算簇内熵值或主要簇的占比。
● 作用：
  ○ 若 Agreement 低（召回分散），后续 Evidence Gate 将提升置信度阈值要求。
  ○ 触发告警：提示检索阶段可能存在意图模糊或知识库覆盖不足。
实体识别评估闭环（补充统计验证）：
系统虽不依赖显式 NER 组件，但通过 Query Rewrite 的 standardized_entities 字段实现实体级控制。为保证召回稳定性，引入离线评估机制：
● ★ **未实现**：每月在标注语料库上计算实体召回 Recall@K 与 F1，并用结果调整 Prompt、检索权重与 RRF 系数。
  仓库中既没有该标注语料库，也没有这条月度评估的调度；`standardized_entities` 目前只是 Rewrite 输出的
  一个字段。检索 benchmark 的 `Recall@1/3/5/10` 是**框架**（`benchmarks/`），结果为 `PENDING`。
7.3 两阶段 Rerank（GPU 批处理化重构）
Stage 1：BiEncoder（宽保留策略，GPU Batch）
● 输入：Union Recall Set（去重后约 150~200 条候选）
● 行为：从各路召回 Top100 中，合并保留 Top 150 条（而非粗暴截断至 40）。
● 目标：保证关键证据不会在早期被过滤，为后续投票留出冗余空间。
● 执行方式：请求内批量编码（`retrieval/bi_encoder.py`，默认保留 Top 150）。
  *设计目标*：GPU1 上的 BiEncoder Batch Service 跨请求合并候选，单请求等效延迟 P99 ≤ 60ms（原 CPU 串行 180ms）——**未实现，未实测**。
Stage 2：CrossEncoder Ensemble（GPU 微批聚合，消除单点性能瓶颈）
● 引入双模型轻量 Ensemble：
  ○ CE-A：法律/成分调优版 CrossEncoder
  ○ CE-B：通用语义 CrossEncoder
● 批处理架构：
  ○ 【已实现】`retrieval/rerank_batch_aggregator.py`，由 `cross_encoder_ensemble.rerank()` 调用，
    按 `max_batch_size` 分块做 **请求内同步** 批量预测，并记录填充率与排队延迟统计。
  ○ 【未接入主链路】跨请求 micro-batching：需显式调用 `submit_batch()`，当前在线路径不调用。
    因此**不得**声称「当前主链路已完成跨请求动态微批」。
  ○ *设计目标*：time-based 10–20ms 窗口、单 pair 200–400ms → 1–3ms、Stage 2 P99 ≤ 60ms——**未实现，未实测**。
● 计算逻辑：CE_score = avg(CE_A(doc), CE_B(doc))
● 输出：Top 10 文档及其经 Platt Scaling 校准的概率分数。
7.4 Evidence Ensemble Gate（从单点判决到置信投票）
❌ 原单点风险：仅依赖 CrossEncoder Top1 分数，系统鲁棒性极低。
✅ 修改为多维度证据投票机制：
综合置信度计算公式：
Evidence Score =
  w1 * CE_Top1_Score +              # CrossEncoder 最强匹配
  w2 * CE_Top3_Mean_Score +         # 冗余文档平均得分
  w3 * Retrieval_Agreement_Score +  # 检索一致性评分（7.2节）
  w4 * Doc_Consistency_Score        # 证据间逻辑矛盾检测（NLI 交叉校验，GPU Batch）
NLI 交叉校验（GPU 批处理优化）：
● 不再逐条 doc 做 NLI，仅对 Top-3 候选文档进行批量蕴含关系判断。
● 在 GPU1 的 Rerank Batch Service 中与 CrossEncoder 共享批次窗口，延迟 ≤25ms。
决策规则表：
Evidence Score	行为
≥ 0.75	放行：高置信，直接进入 LLM 生成
0.55 – 0.75	多证据增强生成：Prompt 中强制注入 Top3 文档摘要并要求 LLM 对比回答
< 0.55	拒答：返回 200 OK 并给出引导性拒答（“无法确认，请补充信息”），与 HTTP 503 严格区分
权重学习闭环：Evidence Score 的权重 w1~w4 取自 `config.json`，是**手工设定的设计值**。
> ★ 通过离线日志学习、优化「回答可用性 × 点击率」相关性并每周更新——**未实现**，见 §12.2。
> Evidence Gate 的阈值同样未经真实标注数据调优，属 `DESIGN_TARGET`。
8. Fail‑safe 机制
● Evidence Ensemble Gate：如上所述，从单一分数判断升级为多因素投票。
● Answer Gate：NLI 模型校验 Answer 与 Top1 Doc 的蕴含关系（GPU 批处理，延迟 ≤25ms）。contradiction>0.5 则标记警告，法规类强制拒答。
● 降级与熔断区分：系统仅在基础设施故障（如 Redis 连接断开、Qdrant 超时）或极端过载（详见第 9 节）时返回 HTTP 503。业务逻辑（如 Rewrite 失败、证据不足）一律返回 HTTP 200 并附带结构化拒答理由，确保 API 契约稳定。
9. 弹性降级与限流
● 令牌桶限流（Redis-State）：法规 60%、研发 30%、闲聊 10%。
● KV Cache 感知渐进降级（与第 5.2.5 节联动，阈值以 `admission/kv_admission.py` 的常量为准）：
  ○ `threshold_tighten`：令牌桶收紧，请求排队
  ○ `THRESHOLD_TRUNCATE`（0.80）：应用层流式截断（采样参数不变），P0 法规收缩输出
  ○ `THRESHOLD_SOFT_STOP`（0.90）：P0 保持 14B，P1 降级至 4B，P2 丢弃
  ○ `THRESHOLD_CRITICAL`（0.95，极度过载）：按优先级分别拒收 / 排队 / 降级收缩，P2 返回 503（服务繁忙稍后重试）。注意：此处 503 是系统级过载保护，与业务逻辑中的“法规请求失败”无关，法规核心场景在业务层已通过兜底策略保证可用性。
  > 本节原写“阈值统一为 70/80/90/95%”，与实现不符，已按代码常量更正（0.80 / 0.90 / 0.95）。
● Prefix Caching 保护：降级时不修改 max_tokens 参数，避免缓存键哈希变更引发 Prefill 风暴。
10. 缓存体系（核心重构：权限原子化与版本分区）
10.1 设计原则：Cache Key 与权限约束原子绑定
缓存设计的根本问题不是“没有版本化”，而是权限模型未参与缓存分区，导致 cache hit ≠ effective hit。本次重构将权限从运行时过滤上升为 Key Space 约束，并通过知识版本 epoch 消除失效传播问题。
10.2 缓存 Key 统一构造（权限 + 版本原子化）
cache_key = hash(
    normalized_query +
    embedding_version +          # 模型 commit SHA（git 哈希）
    knowledge_version_epoch +    # 替代逐条 expiry 判断，来自配置中心
    prompt_version +             # prompt 注册表版本（DB 控制）
    schema_version +             # API 契约版本
    role_mask +                  # 权限进入 key 空间
    dept_mask
)
● embedding_version：**实现只使用 `config["embedding"]["text"]["model_path"]`**（`core/pipeline.py::_build_cache_key()` 的 `"ev"` 字段）。
  > **已知边界**：缓存键**不包含** `embedding.*.model_revision`，也**不包含** CLIP 的模型配置。
  > 因此「模型更新后旧缓存自然失效」只在 model_path 变化时成立；同一个路径下替换权重（revision 变化）
  > 或更换 CLIP 配置**不会**失效缓存，可能读到陈旧答案。若要真正失效，需要把 revision 与 CLIP 配置
  > 一并纳入 key —— 这是实现缺口，不是本文的设计目标。
● prompt_version：**已实现**。版本存储是文件目录 `./data/feedback/rewrite/prompt_versions`
  （不是 DB）；`rewrite/feedback.py::save_prompt_version()` 自动生成 `v{YYYYMMDD}_{序号}` 形式的
  版本号，`activate_prompt_version()` 标记该版本为 active 并调用 `_update_config_prompt_version()`
  **自动改写 `config.json` 的 `generation.prompt_version`**，从而让缓存键随激活而变化、旧缓存自然失效。
  > 本节原写「由 Prompt Registry（DB 控制）管理」——**载体不是 DB，是文件目录**；其余机制成立。
● schema_version：与 API 响应结构强绑定，结构变更时缓存隔离。
● 权限掩码直接参与 Key 计算，相同 query 对不同用户产生不同缓存分区。
● knowledge_version_epoch 是配置中的单一知识版本标识，文档发布/封存后由**人工**切换；旧 epoch 的缓存不再被访问，由 Redis LRU 自然淘汰。
  ★ 「每日滚动的版本号（如 20260411）」与「自动更新全局 epoch」是历史/目标设计，**未实现**（见 §3.6 / §10.5）。
10.3 缓存分层设计（职责分离）
缓存层级	Key 组成	服务范围	命中语义
L1 Public Cache	query_hash + version + role_bucket (public only)	仅存储 role_mask=0 且 dept_mask=0 的全公开文档回答	命中即表示权限通过且内容最新
L2 Private Cache	query_hash + version + role_mask + dept_mask	存储特定权限组合下的回答	Key 已包含权限指纹，命中即表示权限校验通过
● L1 永远只服务 public doc，不混入任何需权限的内容。
● L2 是 RBAC 缓存，不同 role_mask/dept_mask 组合形成独立缓存分区。
10.4 Redis 职责简化（删除运行时权限校验）
● ❌ 原方案：L2 命中后仍需调用 Redis 进行权限二次校验。
● ✅ 修改后：Redis-Cache 仅负责 TTL 管理与 Key 查找，权限判断已在 Key 构造和检索下推阶段完成，命中即表示有效。
● Redis 集群仍保持物理隔离：Redis-Cache（allkeys-lru，容忍丢失）与 Redis-State（noeviction，AOF 持久化），故障时自动降级。
10.5 缓存失效策略：版本切换替代主动删除
● ❌ 原方案：expiry_date 字段更新 → 相关缓存批量删除 → 引发缓存雪崩。
● ✅ 修改后：
  ○ 【当前实现】epoch 的**激活是显式人工发布步骤**：调度器最多执行 build/validate/seal，不自动切换生产 `active_epoch`（见 §3.6）。
  ○ 【历史/目标设计】原设计由 Airflow 在知识库发布/文档过期时自动更新全局 `knowledge_version_epoch`（如 20260411_02）；该自动 activation 未实现。
  ○ 新请求使用新 epoch 生成 Cache Key，旧 epoch 的缓存不再被访问，由 Redis LRU 自然淘汰。
  ○ 无需主动 Invalidate，彻底消除缓存失效风暴。
10.6 语义安全缓存控制
● requires_context=true 的缓存仅限原会话（同一 session_id）复用，不跨用户/跨会话共享。
10.7 CLIP 缓存策略（已移除）
原基于 Query Hash 的 CLIP Embedding Cache 因命中率极低且导致首轮召回失效，改为 Session 级临时缓存与异步预热机制（见 4.5 节）。
11. 权限与安全审计
● 审计日志记录 user_role_mask、过滤表达式、拦截原因。
● 明文保护：user_query 强制 SHA256 哈希，研发配方类查询日志脱敏为 [REDACTED]。
12. 可观测性与数据闭环
● 【当前实现】全链路追踪钩子接入在线主链路，走 OpenTelemetry SDK TracerProvider；span 导出为可选 OTLP 路径（`monitoring/otel_exporter.py`），默认关闭。
● 【历史/目标设计】原「OpenTelemetry + Jaeger」表述对应 Jaeger thrift agent 路径，该配置已从仓库移除且从未有 canonical 消费者。OTel SDK 已不再附带 Jaeger exporter，当前实现走 OTLP；collector/后端/可查询 span 的闭环仍为 `PENDING`，本仓库没有任何 span 被后端查询到。
● **【当前实现】`/api/metrics` 真实存在的 series**
  （`monitoring/otel_tracer.py::MetricsCollector`，由 `record_request()` 在在线路径上记录；
  下列清单由实际驱动 collector 得到，不是设计愿望）：

  计数器：

  ```
  rag_http_requests            rag_http_responses_2xx / _4xx / _5xx
  rag_http_rate_limited        rag_admission_total
  rag_cache_total              rag_cache_hit_L1 / rag_cache_hit_L2 / rag_cache_hit_L2_SESSION
  rag_rewrite_success          rag_rewrite_fail          rag_rewrite_fallback
  rag_degradation_total        rag_blip_total             rag_blip_triggered
  rag_nli_contradiction_high   rag_prefix_cache_hit / rag_prefix_cache_miss
  rag_redis_degraded_events    rag_evidence_decision_<decision>
  ```

  直方图（summary，含 `_count`）：

  ```
  rag_http_request_duration_seconds        rag_evidence_score_seconds
  rag_nli_contradiction_score_seconds
  ```

  Gauge：

  ```
  rag_http_active_requests   rag_redis_degraded_mode   rag_kv_pressure
  rag_otel_exporter_enabled  rag_uptime_seconds
  ```

  vLLM 韧性契约另有 `rag_vllm_generation_*` 系列（`router/vllm_resilience.py`）。

  **比率一律是派生量**，仓库不发布 `*_rate` series：用已 emit 的计数器做 PromQL ratio，
  或读 `/api/stats` 的计算字段（`cache_hit_rate.{L1,L2,L2_SESSION}`、`rewrite_fallback_rate`、
  `nli_contradiction_rate`、`prefix_cache_hit_rate`、`redis_degraded`）。
  由此可知本节**已经可以**得到：L1/L2 命中率、Rewrite 降级率、NLI 矛盾比例、
  Prefix Caching 命中率、降级触发次数、Admission 计数、Evidence Gate 分数分布
  （用 `histogram_quantile`）、BLIP 触发率、KV Pressure。6 条告警规则与 10 面板 Grafana JSON
  只引用其中一部分，且**不引用** `rag_kv_pressure`、`rag_blip_*`、`rag_evidence_*`、
  `rag_nli_*`、`rag_prefix_cache_*`、`rag_admission_total` —— 这些 series 存在但**没有任何告警或面板**。

● **【设计目标】本节最初列出的下列指标确实不存在**，不得据此排障：

  ○ Evidence Score 的**各子项权重贡献**（只有合成分布，无 w1~w4 分解）
  ○ Retrieval Agreement Score 分布（有计算，无 series）
  ○ Shared KV 与 Per-request KV 的**分类**占用率（只有合并的 `rag_kv_pressure`）
  ○ 有效并发数（动态值）与 `max_num_seqs`
  ○ Rerank Batch Aggregator 的 Batch 填充率、排队延迟 P50/P99、GPU 利用率
  ○ 任何 GPU / DCGM / vLLM 调度器 series（本部署没有 GPU exporter）

  > `monitoring/otel_tracer.py` 里的 `AlertingManager` 是一个更早的进程内阈值引擎，**未接入**
  > canonical 请求路径；喂它的 `config.json` → `alerting.rules` 配置块已移除。它不是告警契约。

● 告警：BLIP 触发率 >10%、缓存校验失败突增、KV Cache 持续 >85%、KV Pressure >0.9 持续 30s、Prefix Caching 命中率突降 >50%、Redis 降级持续 >5 分钟、L1/L2 命中率突降 >30%、Rerank Batch 排队延迟 >50ms 等。
12.1 KV Cache 分类与监控
vLLM Prefix Caching 按共享特性分为三类：
1. Shared KV Cache：system prompt、法规前缀模板等，可跨请求复用，命中率反映系统级效率。
2. Per-request KV Cache：用户 query 相关 tokens，不可复用，仅影响单次请求的 Prefill 时间。
3. Prefix Cache 边界条件：仅当 prompt_version + model_id + system_prompt 完全一致时，Shared KV 才可跨请求命中。任何 Prompt 模板修改或模型切换均会导致 Shared KV 失效，需监控重建频率。
KV Pressure 实时监控（与第 5.2.5 节联动）：
KV_Pressure = current_used_kv / max_kv_capacity
该指标驱动 Admission Control 与降级决策，是系统稳定性的核心仪表盘。
12.2 离线反馈闭环（数据驱动优化）
> **状态：`CURRENT`（机制骨架已实现）+ `DESIGN_TARGET`（全部自动学习未运行）。**
> 本节描述的是一个**目标态闭环**。仓库里存在 A/B 平台代码（`common/ab_testing.py`，已接入
> `core/pipeline.py`）、反馈 review 门控（`offline/feedback_loop.py`，只有 `accepted` 记录进入
> 训练导出）和回归候选人工审批；但**没有任何一条自动学习闭环在运行**，也没有配置过任何实验。
> 下面每一条带 ★ 的都是**尚未实现**的设计，不得引用为「系统会每周自动优化」。

● 日志采集：★ **本节所列的采集契约未实现**。记录每次请求的 query、检索 doc_id 列表、
  Rerank 分数、最终 answer、用户行为信号（点击“有帮助”/追问/复制答案）——这五类**都没有**被持久化：
  结构化审计写的是 `user_query` 的 **SHA-256 哈希**加上身份/过滤/拒绝字段，不含 doc 列表、
  Rerank 分数或答案正文；`/api/metrics` 暴露的是聚合量；代码里不存在“有帮助 / 追问 / 复制”这三种
  行为信号的采集，也没有任何在线路由把它们写入 `offline/feedback_loop.py`。
  > 因此**不能**说本系统在积累可用于学习的反馈数据；`offline/feedback_loop.py` 处理的是另一条
  > review 门控的反馈记录，不是在线请求日志的回流。
● 标注与评估：
  ○ ★ 每周采样 500 条日志进行人工标注（回答正确性、完整性、证据充分性）——**未实现，无调度**。
  ○ ★ 离线评估集包含 2000 条标注样本——**未实现**。当前唯一评测数据是
    `tests/evaluation/golden_set.jsonl`（301 条，格式校验，非人工标注质量集）。
● 参数更新：
  ○ ★ RRF 融合权重（w_text/w_clip/w_ocr）基于点击率与人工标注相关性进行贝叶斯优化，每周更新
    ——**未实现**。权重初值与现值均来自 `config.json`，代码中没有权重学习逻辑。
  ○ ★ Evidence Gate 阈值与权重通过 ROC 曲线调整，以最大化 F2 分数——**未实现**。
  ○ ★ Rewrite Prompt 模板根据意图识别准确率进行 A/B 测试迭代——**自动迭代未实现**。
    版本机制本身已实现（`rewrite/feedback.py` 的文件版本目录 + 激活时自动改写
    `generation.prompt_version`，见 §10.2），但没有基于准确率的自动迭代。
  ○ ★ Rerank Batch 聚合参数基于 GPU 利用率和排队延迟进行动态调优——**未实现**。
● A/B 实验平台：`common/ab_testing.py` 的分流、权重/阈值覆盖与指标记录**已实现并接入主链路**，
  但**未配置任何实验、未运行过一轮**。★「关键策略变更需经 1 周 A/B 实验后全量发布」是流程设计，
  不是已运行的发布机制。
13. API 服务拆分
【状态：`HISTORICAL_DESIGN`】当前 canonical 主线是 FastAPI 单体（`app.py`），没有 `/rewrite`、
`/generate` 这两个对外端点；相关逻辑位于 `rewrite/`、`router/` 内部组件中。
● /rewrite（设计）：输入原始 query，输出标准化 JSON（含 business_type/intent），
  **P99 ≤ 45ms（`DESIGN_TARGET`，从未实测）**，max_tokens=192。
● /generate（设计）：接收 rewrite 结果，执行完整 RAG 流程生成答案，
  **P99 ≤ 3.0s（`DESIGN_TARGET`，从未实测）**。
核心设计价值总结
1. Query Rewrite 定位为“路由增强器”而非核心强依赖，失败时降级为规则兜底，消除 503 单点风险。
2. 无状态轻量路由 + 交给 vLLM 原生 continuous batching（历史设计为双 vLLM 实例；当前实现为单一共享 4B 端点 + 14B），废除外部优先级队列调度，消除双层调度冲突与批次碎片问题。
3. CLIP 判别式同步路由 + 异步补充，确保多模态召回在首轮生效，同时控制延迟且实现有效预热。
4. Bitmask 权限前置下推 + 缓存 Key 原子绑定，将权限从运行时过滤上升为索引约束与缓存分区依据，O(1) 复杂度，消除 cache hit ≠ effective hit 的一致性问题。
5. 文档版本 epoch 化管理，用版本切换替代实时 expiry 判断与缓存主动删除，彻底避免缓存失效风暴。
6. 加权 RRF 按查询类型施加静态权重（法规提高 BM25、视觉提高 CLIP），避免多模态融合的“暴力平均”。
   *设计目标*：权重进一步由离线日志学习与 A/B 实验校准——**该学习环节未实现**，见 §12.2。
7. 上下文重建 + 约束式完整生成取代传统续写，从根本上保证长文本语义一致性，杜绝重复、逻辑断裂与前后矛盾。
8. KV-aware Admission Control + Token-Level 并发控制，将系统并发从静态配置升级为动态 KV 预算调度，构建统一的吞吐模型（QPS ≈ 有效并发 / 延迟），消除原指标不自洽问题，确保在 vLLM continuous batching 下 GPU KV Cache 利用率稳定在安全阈值内，从根本上消除 OOM 风险。
  ○ KV Cache 建模从“MB/token 常数”升级为“基于层数/GQA 的结构化估算”。
  ○ 并发模型从“静态并发数”升级为“token 分布驱动的 KV occupancy 动态模型”。
  ○ 多 vLLM 实例 KV 模型修正：明确 KV pool 为统一资源池，多实例导致 partition 与 fragmentation 风险。
9. KV Cache 感知降级 + Prefix Caching 保护，避免降级引发缓存雪崩；明确区分 Shared/Per-request KV Cache。
10. Redis 职责分离 + 降级容灾，消除单点故障域，Cache 层不再承担权限校验职责。
11. 检索鲁棒性重构：
  ○ 并行多路召回替代三段式递归，消除延迟翻倍与召回不稳定性。
  ○ CrossEncoder Ensemble 与 Evidence Ensemble Gate 取代单点判决，引入检索一致性评分与多维度投票机制，将系统从“串行过滤链”升级为“并行证据系统”。
  ○ 实体识别召回能力通过离线评估集（Recall@K、F1）量化监控，保证召回稳定性可验证。
12. 文档生命周期闭环与稀疏权限召回兜底，保障召回质量与合规性。
13. 重模型推理 GPU 批处理化（设计目标，尚无可复现性能验证）：
  ○ 目标是在 GPU1 批处理 CrossEncoder、NLI、BiEncoder、CLIP Text Encoder、BLIP，并用 Rerank Batch Aggregator 聚合微批。200–400ms、30–60ms 和 QPS 改善均为设计估值，不代表当前生产实测。
  ○ CPU 回归轻量逻辑层（routing/feature assembly/metadata filter/cache lookup），系统从“GPU 闲置 + CPU 爆炸”的反模式转变为“GPU 计算 + CPU 编排”的最佳实践。
  ○ 批处理参数（窗口时间、batch size）纳入离线反馈闭环，实现数据驱动的持续优化。
14. 数据驱动闭环（**设计目标**）：目标形态是「规则 + 统计反馈 + 可校准参数」。
    当前状态：机制骨架存在（A/B 分流已接入主链路、反馈 review 门控、回归候选人工审批、
    prompt 版本管理），但**没有任何一条自动学习闭环在运行**，也没有配置过任何实验。
    权重与阈值目前由 `config.json` 决定。因此本系统当前是「规则完备 + 可校准参数」，
    **不是**「可自学习系统」。逐条边界见 §12.2。
