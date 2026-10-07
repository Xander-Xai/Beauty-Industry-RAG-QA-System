# 化妆品行业 RAG 问答系统 · Cosmetics Industry RAG QA System

> **一句话定位**：企业内部多模态 RAG 问答系统——把散落在 PDF、图片、表格里的化妆品法规 / 成分 / 产品知识变成可检索资产，在线返回**带引用、带出处、证据不足就拒答**的答案，并按角色与部门做权限隔离。
>
> **技术含量在哪**：不是"接了个向量库"。在线链路是**动态 2–4 路召回 → 两级重排 → Evidence/Answer 双 Gate 拦截幻觉 → RBAC 纵深防御 → 4B/14B 模型路由**，离线侧是**多模态解析 + 知识版本封存 / 人工激活**的分发布纪律。
>
> **最想展示的能力是证据治理**：每条能力都带 canonical 证据等级，`scripts/check_repo_consistency.py` 在 CI 中拦截该守卫覆盖的口径漂移——证据等级越界、文档链接与路径失效、枚举与 SLO 计数对不上、引用不存在的指标 series。它只校验这些可机械判定的契约，**不判断任意自然语言陈述的真实性**。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.10 | 3.11](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)
![React 18](https://img.shields.io/badge/React-18-61dafb.svg)
![Qdrant](https://img.shields.io/badge/Qdrant-vector-24474e.svg)
![Elasticsearch 8](https://img.shields.io/badge/Elasticsearch-8.x-005571.svg)
![Redis](https://img.shields.io/badge/Redis-session%20%2F%20cache%20%2F%20rate%20limit-d82c20.svg)
![Evidence: REPO_VERIFIED ≠ production-validated](https://img.shields.io/badge/evidence-REPO_VERIFIED%20%E2%89%A0%20production--validated-6f42c1.svg)

[![CI](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/actions/workflows/ci.yml)
[![Lint](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/actions/workflows/lint.yml)
[![Security](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/actions/workflows/security.yml/badge.svg?branch=main)](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/actions/workflows/security.yml)

上面三个徽章是 **GitHub Actions 动态徽章**，直接读取 `main` 上的真实运行结果——绿色就是绿了，红色就是红了。本 README **不对 CI 结果做任何文字声明**；工作流定义见 `.github/workflows/`，实际运行状态以 Actions 页面为准。Runtime version `2.3.0`（`config.json` → `system.version`）。

---

## 30 秒读懂

给面试官的第一屏。每一行都能在本仓库里打开对照。

| # | 问题 | 答案 | 深挖入口 |
|---|---|---|---|
| 1 | **这是干什么的** | 把法规 / 成分 / 产品知识建成可检索资产，在线回答带引用的领域问题，证据不足就拒答 | [Architecture](#architecture) |
| 2 | **解决什么业务问题** | 知识散在 PDF、图片、表格里，人工检索慢且回答容易编造；不同角色该看到的内容也不同 | [核心工程能力](#核心工程能力5-项) |
| 3 | **RAG 架构的技术含量** | 动态 2–4 路召回 + 加权 RRF、BiEncoder/CrossEncoder 两级重排、Evidence Gate + Answer Gate 双门控、4B/14B 路由 | [Architecture](#architecture) · [docs/interview-walkthrough.md](docs/interview-walkthrough.md) |
| 4 | **企业工程化体现在哪** | RS256 认证 + uint32 位掩码 RBAC（存储下推 + 融合前二次过滤 + 缓存物理分区）、结构化业务动作审计、Prometheus 指标与告警规则、SLO/故障 Runbook、Docker Compose + Kubernetes 双形态 | [docs/operations-guide.md](docs/operations-guide.md) · [docs/slo-runbook.md](docs/slo-runbook.md) |
| 5 | **历史生产规模** | `HISTORICAL_PRODUCTION`（**上一家公司生产环境，不是本仓库 benchmark**）：3000+ 文档、5000+ 图片、200+ 用户、10–15 QPS、日均 1500+ 请求、RTX A5000 ×2 | [Historical Production Context](#historical-production-context) |
| 6 | **哪些是 repo verified** | `REPO_VERIFIED`（代码 + 确定性测试覆盖）：主链路全流程、两级重排、双 Gate、RBAC、认证、指标端点、审计、告警规则、benchmark/性能**框架** | [Evidence Matrix](#evidence-matrix) |
| 7 | **哪些还没有真实验证** | `PENDING`：真实 QPS/延迟产物、真实检索 benchmark 结果、真实 RAGAS 分数、OTLP 运行期闭环尚未跑通、告警在生产触发、真实 4B/14B vLLM GPU 部署、真实 BGE/CLIP/PaddleOCR smoke | [Repository-Reproducible Evidence](#repository-reproducible-evidence) |
| 8 | **怎么进入架构 / 代码 / demo** | 架构：本页 Architecture 图 · 代码：`core/pipeline.py` + `retrieval/parallel_recall.py` · Demo：下方演示图与复现命令 · 5 分钟面试脚本：[docs/interview-walkthrough.md](docs/interview-walkthrough.md) | [Quick Start](#quick-start) |

### 六级证据，只用这一套词

仓库的 canonical taxonomy 只有下面**六级**，与 [docs/interview-evidence-map.md → Classification vocabulary](docs/interview-evidence-map.md#classification-vocabulary) 完全一致；本表是它的完整呈现，不是子集，也不存在第二套证据词汇。

| 等级 | 一句话 | 在本仓库对应什么 |
|---|---|---|
| `HISTORICAL_PRODUCTION` | 前雇主生产环境事实 | 业务规模与流量背景，**不是**本仓库测量结果 |
| `HISTORICAL` | 本仓库内已被取代、只为追溯保留的实现 / 配置 / 设计 | 保留的旧阈值引擎 `monitoring/otel_tracer.py` 里的进程内 `AlertingManager`（未接入主链路）；**不是**当前能力，也**不是**前雇主生产事实 |
| `REPO_VERIFIED` | 这里实现了，且有测试覆盖 | 绝大部分主链路能力 |
| `LOCAL_REAL_VALIDATION` | 本地对真实依赖跑过 | **只有 5 项**（Redis 会话/限流、nginx 代理信任、认证 ES、认证 Prometheus 抓取） |
| `DESIGN_TARGET` | 写进文档的目标值 | 5 个 SLO 目标与全部告警阈值 |
| `PENDING` | 代码在，验证所需的真实资产/运行时/凭据拿不到 | 所有"跑过才知道"的结论 |

`HISTORICAL` 与 `HISTORICAL_PRODUCTION` 不可互换：前者说的是本仓库自己的历史代码，后者说的是前雇主的真实生产系统，两者都不是当前能力。

---

## Historical Production Context

> ### ⚠️ `HISTORICAL_PRODUCTION` — 下表全部是**上一家公司生产环境**的业务规模与流量背景
>
> **本公开仓库不包含**对应的专有语料、生产日志、模型权重、监控数据或流量切分配置，因此这些数字**不是** `REPO_VERIFIED`，**不是**本仓库的 benchmark，**也无法由本仓库复现**。请勿把它们当成本仓库的测量结果引用。

| 维度 | 历史生产环境事实 |
|---|---|
| 知识资产 | 3000+ 文档 · 5000+ 图片 · 1500+ 产品 · 2000+ 成分 · 8 大法规体系 |
| 用户与流量 | 200+ 内部用户 · 高峰短时 10–15 QPS · 日均 1500+ 请求 |
| 生产推理硬件 | RTX A5000 ×2 |
| 后续模型迁移 | Qwen2.5 → Qwen3-14B / Qwen3-4B 灰度迁移验证 |
| 公司认可 | 年度技术创新奖 |

三条不可跨越的边界：

1. **不得**把上表任何一项归类为 `REPO_VERIFIED`——本仓库没有任何代码、配置、测试或产物能证明服务硬件、灰度迁移或奖项。
2. **不得**用历史生产经验替代仓库验证：`config.json` 里 4B / 14B vLLM 拓扑**在本仓库从未执行过**（权重缺失、`vllm` 未安装），该项为 `PENDING`。
3. **奖项不是运行时验证**，它不携带关于本仓库延迟、吞吐或正确性的任何证据。

> **10–15 QPS 与 1500+ 日请求是生产观测值，不是本仓库 benchmark**——本仓库没有可复现的 QPS / 延迟 benchmark 结果。逐行边界与完整版见 [docs/interview-evidence-map.md](docs/interview-evidence-map.md) 的 *Business scale — historical production context* 一节。

---

## 一次请求长什么样（合成数据演示）

![合成数据演示：用户 Query → 带引用的回答 → 引用证据 → 来源文档 → 权限与可信证据。左侧为本仓库前端在 Chromium 中的真实渲染，右侧为演示标注](docs/assets/demo-request-evidence-flow.webp)

五段链路：**用户 Query → 回答（内含〔证据N〕引用）→ 引用证据标签（可点，命中来源文档）→ 来源文档条款 → 权限 / 可信证据**。第二问是切换身份之后的同一问题：`Regulatory Affairs`（role_mask=0x04 · dept_mask=0x04）读得到 ④ 里那两份文档，两份的掩码都是 0x04 / 0x04；切到 `Commercial Team`（0x08 / 0x08）后两份都被权限过滤掉、证据为空，系统拒答而不是编一个答案。

这处对照不是口头承诺：合成语料里每份文档的 `role_mask` / `dept_mask` 都由 `tests/test_demo_corpus_rbac_consistency.py` 用**真实的 `common.auth.is_allowed`** 逐份校验，mock 也按同一谓词逐份过滤，不会端出当前身份打不开的引用。这只证明演示数据与仓库的权限语义自洽，**不构成 RBAC 的运行时验证**。

这张图的边界，先说清楚：**左侧是真实 UI**（`frontend/src/App.jsx`，由 Playwright 驱动真实输入与点击：提问、切换身份、发送），后端是 `docs/demo/mock_api.py` 这个合成 mock；**右侧两张卡片是演示标注**，不是产品界面，每一行都指向仓库里真实实现它的文件；**全部数值是合成的**（`docs/demo/synthetic_corpus.json`）——虚构文档号、占位 CAS 号、杜撰标准名，不含真实法规结论、上一家公司语料、生产日志、凭据或流量数据，**也不是**任何性能或质量测量结果。复现命令 `python3 docs/demo/capture_demo.py`（说明见 [docs/demo/README.md](docs/demo/README.md)）；本仓库**没有**公网 Demo 与演示视频，因此没有 Demo 链接可点。

---

## Architecture

> 当前已验证架构是 **FastAPI 单体主链路**，不是已完成联调的微服务架构。完整事实基线（唯一的架构口径来源）见 [docs/interview-architecture-baseline.md](docs/interview-architecture-baseline.md)。

```mermaid
flowchart LR
  FE["React frontend"] --> API["FastAPI 单体<br/>app.py"]
  API --> ID["身份解析<br/>uint32 role_mask / dept_mask"]
  ID --> CACHE["L1 进程内 / L2 Redis<br/>知识版本 + 权限指纹"]
  CACHE --> REC["动态 2–4 路召回<br/>dense_bge · bm25_es · rewrite_variant · clip_visual"]
  REC --> FUSE["文档级 RBAC 二次过滤 + 加权 RRF"]
  FUSE --> RERANK["BiEncoder 宽保留 → 双 CrossEncoder 精排"]
  RERANK --> GATES["Evidence Gate → 共享 4B / 14B 生成 → Answer Gate"]
  GATES --> OUT["答案 + 引用 + 审计字段"]
```

上图为**第一屏简化视图**。完整 canonical 架构图（含离线知识构建链路与 Qdrant / Elasticsearch / Redis / vLLM 依赖）见 [docs/interview-architecture-baseline.md](docs/interview-architecture-baseline.md)，它也是唯一的架构口径来源。微服务目录（`api-gateway/`、`retrieval-service/`、`generation-service/`、`monitoring-service/`）是保留的代码组件，**不代表**已与当前前端完成端到端生产验证；当前默认主线是上面的单体应用。

---

## 核心工程能力（5 项）

> 每一项的等级都是 `REPO_VERIFIED`（代码 + 确定性测试覆盖），**不是** `LOCAL_REAL_VALIDATION`，更不是生产验证。逐项的等级、代码位置、测试位置与升级路径见 [Evidence Matrix](#evidence-matrix) 与 [docs/interview-evidence-map.md](docs/interview-evidence-map.md)。

### 1 · 动态 2–4 路召回 + 两级重排，而不是固定四路

简单问题 2 路（`dense_bge` + `bm25_es`）；复杂问题 +1 路 `rewrite_variant`；复杂且视觉相关再 +1 路 `clip_visual`。ES Fallback 是降级补召回，不算一路。融合后 BiEncoder 宽保留 Top 150，再由双 CrossEncoder ensemble 精排到 Top 10（请求内批量预测）。加权 RRF 按问题类型调整权重：法规类提高 BM25，视觉类提高 CLIP。
`retrieval/parallel_recall.py` · `retrieval/bi_encoder.py` · `retrieval/cross_encoder_ensemble.py`

### 2 · 双 Gate 把幻觉关在门外

生成前 **Evidence Gate** 综合 Top 1 / Top 3 / 多路一致性 / 文档间一致性，输出「正常生成 / 增强证据后生成 / 拒答」；生成后 **Answer Gate** 校验答案与核心证据一致性，法规类矛盾直接拒答。拒答是业务需求，不是装饰。
`retrieval/evidence_gate.py` · `retrieval/answer_gate.py`

### 3 · 权限与信任边界都是纵深的——但只是纵深防御

存储侧下推（Qdrant 下推文档状态与知识版本；ES 下推状态、版本、role、dept）+ 融合前 uint32 位掩码 RBAC 二次过滤；Redis L2 物理 key 按 `rag:l2:rm:{role_mask}:dm:{dept_mask}:{key}` 分区，由 cache 对象自身强制，旧的无分区 key 不回落（宁可 miss）。签名有效只证明 token 来自持钥方；畸形授权声明 **fail closed**。认证以 RS256 为准，旧 HS256 `JWT_SECRET` 仅为可选兼容回退。登录限流 5 次/分钟，仅当 TCP 对端属于 `TRUSTED_PROXIES` 时才解析 `X-Forwarded-For`（`app.py` 设 `proxy_headers=False`，使应用策略具备权威性）。

检索证据按不可信数据处理：保留标记集中定义并在所有不可信通道转义，证据被限定在 `<retrieved_context>` 数据区块内、当前请求在 `<user_query>` 区块内。这是 **prompt 层结构约束，不是强隔离**——它不解决 prompt injection，也不能证明越狱不可能；同样不代表 RBAC 已在生产环境验证。
`common/auth.py` · `api/routes_auth.py` · 逐项控制、测试与仍然开放的缺口见 [docs/security-regression-coverage.md](docs/security-regression-coverage.md)

### 4 · 离线知识构建与在线检索分离，知识版本有发布纪律

多格式解析：UTF-8 TXT、PDF（文本页 + 扫描页 OCR 路由）、DOCX、XLSX，以及独立图片（PNG/JPEG/WebP/BMP/TIFF）。确定性切块默认 500 字符 / 10% 重叠；`doc_id` / `chunk_id` / `image_id` 为逻辑身份，物理 Qdrant point ID 按 epoch 版本化。增量状态检测以内容哈希为准（不是 mtime/size 短路）、snapshot carry-forward、全量重建、快照校验、epoch 封存。反馈闭环统一 review 门控，只有 `accepted` 记录进入训练导出。

构建 / 校验 / 封存（seal）可自动化，但**激活 `knowledge_version_epoch` 是显式人工步骤**；封存后 epoch 不可变，`--skip-validation` 是明确的危险逃生口。cron / Airflow / CLI 共用同一业务逻辑，调度器**从不**自动激活 epoch——DAG 代码存在不等于调度器在运行。
`offline/` · `run_offline.py` · `dags/` · 操作细节见 [数据管理手册](docs/data-admin-guide.md)

### 5 · 仓库自身的证据治理——这是本项目最想展示的能力

一套 canonical 证据等级（`HISTORICAL_PRODUCTION` / `HISTORICAL` / `REPO_VERIFIED` / `LOCAL_REAL_VALIDATION` / `DESIGN_TARGET` / `PENDING`）把"设计目标 / 已实现 / 本地真实验证 / 历史生产"四种话术在文档层面隔离，并由 `scripts/check_repo_consistency.py` 在 CI 里强制：不得把设计目标写成实测、不得引用不存在的指标 series、不得把 RAGAS 缺失写成零分、不得把未测量写成 `0`。上述被守卫覆盖的漂移会在 CI 里被拦截；守卫校验的是这些可机械判定的契约，不判断任意自然语言陈述的真实性。

运维侧同样只认真实存在的东西：`/api/metrics`（需认证）、6 条只引用真实 emit 指标的 Prometheus 告警规则、10 面板 Grafana JSON、9 字段结构化业务动作审计（Redis Stream + 每日 JSONL）、按代码中真实存在的降级路径编写的 SLO + 故障 Runbook，以及**已实现但默认关闭**的 OTLP exporter。

---

## Repository-Reproducible Evidence

| 口径 | 事实 |
|---|---|
| **仓库规模（可复核）** | 当前代码规模约 **6.5 万+ 行 tracked Python**、测试规模 **2,000+ 个 pytest 用例**；CI 覆盖 Python 3.10 与 3.11。为避免快速迭代导致 README 中的精确计数漂移，精确文件数 / 行数 / 收集用例数不在这里静态写死，按下方复核命令在当前 commit 现场计算。 |
| **已实现且有测试覆盖**（`REPO_VERIFIED`） | FastAPI 主链路、离线管线全流程、加权 RRF、两级重排、双 Gate、4B/14B 路由契约、RS256 认证、uint32 RBAC、检索信任边界、指标端点、结构化审计、性能产物**框架**、告警规则、Grafana JSON、SLO/Runbook 文档、OTLP exporter 实现、检索 benchmark **框架** |
| **框架 ≠ 结果** | 检索 benchmark（Recall@1/3/5/10、HitRate@1/3/5/10、MRR@10、NDCG@10）与性能产物七文件契约都是 `REPO_VERIFIED`（框架）/ `PENDING`（结果）——仓库内**没有任何可复现的真实 benchmark artifact**，因此全仓库不引用任何检索指标数字 |
| **性能数字** | **本仓库没有可复现的 QPS / 延迟 benchmark 结果。** 仓库中的性能数值仅允许在 `HISTORICAL_PRODUCTION`（历史生产观测，如 10–15 QPS）、`DESIGN_TARGET`（SLO / 告警阈值）或 `SYNTHETIC DEMO`（README 演示夹具中的合成值）三类明确语义下出现，**均不得表述为本仓库实测结果**；5 个 SLO 目标与告警阈值都是 `DESIGN_TARGET`，不是实测 |
| **本地真实验证**（`LOCAL_REAL_VALIDATION`，只有这 5 项） | Redis 多进程会话持久化 · Redis 跨进程登录限流 · nginx / `TRUSTED_PROXIES` 客户端 IP 解析 · 认证 Elasticsearch 8.11 · 带 Bearer token 的 Prometheus 抓取 |
| **仍为 `PENDING`** | 真实性能产物 · 真实检索 benchmark 结果 · 真实 RAGAS 分数 · OTLP 运行期闭环尚未跑通 · 告警在生产触发 · Grafana 面板被真实数据填充 · 真实 4B/14B vLLM GPU 部署 · 真实 BGE/CLIP/PaddleOCR smoke · QLoRA 训练产物 · 真实 Airflow DAG 执行（逐项与升级路径见下方 [Evidence Matrix](#evidence-matrix)） |

复核方式（只用 `git`、`grep`、`wc`、`awk` 与本仓库的 Python，不依赖 `bc` 等未声明的系统包）：`git ls-files '*.py' | xargs wc -l`、`git ls-files '*.py' | xargs grep -hcE '^\s*(async )?def test_' | awk '{s+=$1} END {print s}'`、`python3 -m pytest --collect-only -q`、`python3 scripts/check_repo_consistency.py`。

**评测的诚实边界**：RAGAS harness / reporter / validator 与 golden set 存在（最初 seed 27 条，现 300+ 条，实际条数以 `validate_golden_set` 输出与 `golden_set.jsonl` 为准），但**格式校验通过 ≠ 领域事实正确**。RAGAS 是隔离的可选 evaluator，不在默认依赖中；库级 `evaluate()` 保留 evaluator-unavailable fallback，**该结果不是质量结果**；使用 `--require-ragas` 运行 strict / real evaluator CLI 时，缺少 evaluator dependency 或 evaluator credential 会 **fail fast**，返回非零状态且不生成 quality report。当前仓库**没有**经过验证的真实 RAGAS quality score。

**生成的诚实边界**：当前生成拓扑是**单一共享 4B vLLM 端点**（`config.json` → `gpu1.models.vllm_4b`，端口 8101）承担 Query Rewrite 与简单生成，复杂生成路由到 `gpu0.models.gen_14b`（Qwen3-14B）；旧 PRD 的双实例拓扑是历史/目标设计，不是当前实现。KV 压力过高时可截断、降级或拒绝。

**追踪的诚实边界**：追踪钩子已接入主链路，OTLP exporter 已实现但**默认关闭**（`OTEL_EXPORT_ENABLED`，可选依赖隔离在 `requirements-otel.txt`），失败不影响业务，span 属性走白名单；**运行期闭环为 `PENDING`**——应用 → exporter → collector → 后端 → 查到 span 这条链路没有任何运行记录，本仓库没有一个 span 被后端查询到过。

---

## Quick Start

### 依赖与配置

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
```

运行时配置由根目录 `config.json` 与 `common/config.py` 管理；敏感值通过环境变量提供。启动前请检查 `.env.example`。

### 启动 API

```bash
python3 app.py
```

默认地址 `http://localhost:8000`；API 文档 `/docs`；健康检查 `GET /api/health`（公开）。

### 前端

```bash
cd frontend
npm ci
npm run build
```

### Docker Compose

Docker Compose 是本仓库的 canonical 部署形态。

```bash
docker compose up -d
```

需要 Redis、Qdrant、Elasticsearch 等服务。安全相关环境变量**缺失时 Compose 会 fail-fast，这是有意行为**：

- `REDIS_PASSWORD`：Redis 启用 `requirepass`。
- `ELASTICSEARCH_PASSWORD`：Elasticsearch 启用 `xpack.security.enabled=true`，客户端使用 `elastic` 用户 + 该密码（`ELASTICSEARCH_USERNAME` 默认 `elastic`）。
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`、`SERVICE_AUTH_TOKEN`。
- 生产环境还需 `CORS_ORIGINS` 与 JWT 密钥（`JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` / `JWT_ALGORITHM=RS256`）。
- 反向代理部署若需按真实客户端 IP 限流，显式设置 `TRUSTED_PROXIES`（逗号分隔 IP/CIDR）；未设置时不信任 `X-Forwarded-For`。

### Kubernetes（第二形态，非 canonical）

`deploy/k8s/` 提供 API 网关的最小 Deployment / Service / ConfigMap / Secret 契约与 31 项静态检查。证据等级仅 `REPO_VERIFIED`（YAML 与契约校验）；**本仓库没有集群，真实部署为 `PENDING`**。探针契约与已知边界见 [Kubernetes 部署契约](docs/deployment-guide-k8s.md)。

### 离线知识构建

```bash
python3 run_offline.py create-index
python3 run_offline.py full-rebuild --epoch phase_2
python3 run_offline.py seal-epoch --epoch phase_2
python3 run_offline.py incremental-build --from-epoch phase_1 --to-epoch phase_2
```

`seal-epoch` 默认先做完整快照校验（Qdrant text/image + Elasticsearch）再封存；`--skip-validation` 是明确的危险逃生口。封存后需要操作者**手动**把 `config.json` 的 `knowledge_version_epoch` 切到新 epoch 并重启在线服务。操作细节见 [数据管理手册](docs/data-admin-guide.md)。

`export-regression-candidates` 把已审核的负向反馈变成待人工审批的回归候选，只有人工接受后才进入回归数据集，详见 [RAGAS 评估指南 §9](docs/ragas-evaluation-guide.md)。

### 检索 benchmark 框架

```bash
# 查看当前环境实际可执行的配置（实时探测，不使用替身 retriever）
python3 -m benchmarks.retrieval_benchmark --list-configs

# 尝试真实运行
python3 -m benchmarks.retrieval_benchmark --config bm25 --limit 5
```

框架为 `REPO_VERIFIED`；**结果为 `PENDING`**。缺少真实依赖时配置以 `BLOCKED` 与原因记录，**不会**产出数字。数据质量缺口见 [docs/benchmark-data-quality.md](docs/benchmark-data-quality.md)，artifact 契约见 [artifacts/benchmarks/README.md](artifacts/benchmarks/README.md)。

### 开发与检查

```bash
python3 -m pytest tests/ -v --tb=short
ruff check .
ruff format --check .
python3 scripts/check_repo_consistency.py
```

`scripts/check_repo_consistency.py` 是本仓库的证据一致性守卫：它校验离线 CLI 子命令真实存在、告警只引用真实 emit 的指标、文档证据等级不越界、性能产物"未测量即 `null`"等契约。安全扫描是独立工作流（`.github/workflows/security.yml`）。

---

## 5 分钟面试 Walkthrough

**[→ docs/interview-walkthrough.md](docs/interview-walkthrough.md)** 是一份可 screen-share 的 5 分钟脚本：按「业务背景 → 整体架构 → 一次 Query 如何穿过系统 → Hybrid Retrieval → Rerank → 双 Gate → 多模态 ingestion → RBAC → Cache → 模型路由 → Observability → Evaluation → Docker/K8s → 失败与降级 → 未验证边界」逐节给出**要说的话**和**要打开的真实代码文件**。面试时按节跳，不用背。

技术面试官的精简问答，每条都标了证据等级——**这本身就是我想让你看到的能力：把"实现了"和"验证过"分开。**

**Q1 · 这个系统解决什么业务问题？**
化妆品行业内部知识问答：法规条款、成分机理、产品资料分散在 PDF / 图片 / 表格里，检索体验差且回答容易编造。系统把多模态知识建成可检索资产，在线给出**带证据、带出处、证据不足就拒答**的答案，并对不同角色和部门做权限隔离。拒答机制（双 Gate）是业务需求，不是装饰。

**Q2 · 系统规模有多大？**
分两个口径，不能混：
- **本仓库可复核规模**：约 **6.5 万+ 行 tracked Python / 2,000+ 个 pytest 用例**，CI 覆盖 Python 3.10 与 3.11；精确计数按 [Repository-Reproducible Evidence](#repository-reproducible-evidence) 中的命令在当前 commit 现场复核，不把快速变化的数字写死在 README。
- **历史生产规模**（`HISTORICAL_PRODUCTION`，不可由本仓库复现）：3000+ 文档、5000+ 图片、1500+ 产品、2000+ 成分、8 大法规体系、200+ 内部用户、高峰短时 10–15 QPS、日均 1500+ 请求，RTX A5000 ×2 推理，后阶段完成 Qwen2.5 → Qwen3-14B/4B 灰度迁移。
本仓库**没有任何 benchmark artifact**，所以我不给本仓库报任何 QPS 或延迟数字。

**Q3 · 核心架构是什么？**
FastAPI 单体主链路 + React 前端。离线侧：解析 → 切块 → BGE 文本向量 / CLIP 图像向量写入 Qdrant、文本写入 Elasticsearch。在线侧：身份解析 → 带知识版本与权限指纹的 L1/L2 缓存 → Query Rewrite 与复杂度判断并行 → 动态 2–4 路召回 → 文档级 RBAC 二次过滤 + 加权 RRF → BiEncoder 宽保留 + 双 CrossEncoder 精排 → Evidence Gate → 4B/14B 路由生成 → Answer Gate。生成拓扑是**单一共享 4B 端点**承担 rewrite 与简单生成，复杂请求走 14B。

**Q4 · 你想展示的核心工程能力是什么？**
- **证据分级与文档治理**：仓库自带一套 canonical 证据等级，并用 `scripts/check_repo_consistency.py` 在 CI 里强制"不得把设计目标写成实测""不得引用不存在的指标 series""不得把 RAGAS 缺失写成零分"。这些被守卫覆盖的漂移会被 CI 拦住。
- **降级路径是真的**：Redis→进程内会话、ES→空结果走 dense、Qdrant→BM25-only、rewrite→简单档位，SLO Runbook 按这些真实降级路径编写，不是通用模板。
- **权限纵深防御**：存储下推 + 融合前位掩码二次过滤 + L2 缓存物理分区，旧 key 不回落（宁可 miss）。
- **发布纪律**：知识版本 seal 后不可变，激活是显式人工步骤；schema 变更 fail closed 而非静默回退。

**Q5 · 哪些是历史生产事实？**
[Historical Production Context](#historical-production-context) 一节全部内容。**边界**：专有语料、生产日志、模型权重、监控数据都不在本仓库，所以这些不是 `REPO_VERIFIED`，也不可复现；奖项是公司认可，不是运行时技术验证；本仓库的 4B/14B vLLM 拓扑从未在这里执行过。

**Q6 · 哪些是本仓库可复现的？**
`REPO_VERIFIED`：FastAPI 主链路、离线管线全流程、RRF、两级重排、双 Gate、4B/14B 路由契约、RS256 认证、uint32 RBAC、检索信任边界、指标端点、结构化审计、性能产物**框架**、告警规则、Grafana JSON、SLO/Runbook 文档、OTLP exporter 实现、检索 benchmark **框架**。
`LOCAL_REAL_VALIDATION`（本地单主机 + 真实依赖，只有这五项）：Redis 多进程会话持久化、Redis 跨进程登录限流、nginx / `TRUSTED_PROXIES` 客户端 IP 解析、认证 Elasticsearch、带 Bearer token 的 Prometheus 抓取。
`PENDING`：真实性能产物、真实检索 benchmark 结果、真实 RAGAS 分数、OTLP 运行期闭环、告警在生产触发、Grafana 面板被真实数据填充、真实 4B/14B vLLM GPU 部署、真实 BGE/CLIP/PaddleOCR smoke、QLoRA 训练产物、真实 Airflow DAG 执行。

**Q7 · 怎么跑起来？**
见 [Quick Start](#quick-start)。`python3 -m pip install -r requirements.txt && cp .env.example .env && python3 app.py`，然后 `http://localhost:8000/docs`。全量校验：`python3 -m pytest tests/ -v --tb=short`。

---

## Evidence Matrix

完整逐条表格（能力 / 等级 / 代码证据 / 测试证据 / 升级路径）见 [docs/interview-evidence-map.md](docs/interview-evidence-map.md)；实现与证据状态的完整审计见 [docs/repository-truth-audit.md](docs/repository-truth-audit.md)。本节是摘要。

### Canonical 证据等级

唯一权威定义在 [docs/interview-evidence-map.md → Classification vocabulary](docs/interview-evidence-map.md#classification-vocabulary)，全仓库（含本 README）不使用第二套词汇：

| 等级 | 含义 | 可以这样说 | 不能这样说 |
|---|---|---|---|
| `HISTORICAL_PRODUCTION` | 前雇主生产环境实际做过的工作；专有资产不在本仓库 | "我在上一套生产系统里……" | "本仓库证明了这个规模" |
| `HISTORICAL` | 本仓库内被取代的实现 / 配置 / 设计，仅保留沿革 | "这是被取代的仓库路径，保留作为历史/兼容上下文" | "这是当前生产能力" |
| `REPO_VERIFIED` | 代码/配置存在，并被本仓库收集到的确定性测试或 CI 覆盖 | "已实现且有测试覆盖" | "已通过生产验证" |
| `LOCAL_REAL_VALIDATION` | 在本仓库用真实外部依赖（Redis / nginx / 认证 ES / 认证 Prometheus）在单台本地主机上跑过 | "在本地对真实依赖验证过" | "生产集群 / HA / SLO 已验证" |
| `DESIGN_TARGET` | PRD 或计划里记录的目标 / 设计；无实现或无可复现 benchmark | "设计目标是……" | "运行中的系统达到……" |
| `PENDING` | 代码可能在，但验证所需的真实资产 / 运行时 / 凭据在此不可得 | "已实现，真实验证待补" | "已经验证过了" |

`EXECUTED` / `PARTIAL` / `BLOCKED` / `PASS` / `NOT RUN` 是**单次运行结果**，不是证据等级，永不出现在证据等级列。

### 框架已实现 ≠ 结果已产出

这是本仓库最重要的一张表。**每一行都是"实现"与"结果"分开的**：

| 能力 | 实现 | 结果 | 缺什么才能升级 |
|---|---|---|---|
| 检索 benchmark（Recall/NDCG/MRR…） | `REPO_VERIFIED` | `PENDING` | 一次真实 ES/Qdrant 运行并提交可复现 artifact（含 `git_sha` / 数据集 sha256 / 模型 revision / 硬件 / 样本数 / 延迟 / 命令 / 限制说明，见 [验收标准](docs/interview-evidence-map.md#benchmark-artifact-acceptance-criteria)） |
| 性能产物契约（七文件、`null` 不写 `0`） | `REPO_VERIFIED` | `PENDING` | 对真实 API + LLM + 检索栈执行既定负载并提交一份 artifact |
| QPS / 延迟数字 | — | `PENDING` | 同上：**本仓库没有可复现的 QPS / 延迟 benchmark 结果**。性能数值仅在 `HISTORICAL_PRODUCTION` / `DESIGN_TARGET` / `SYNTHETIC DEMO` 语义下出现，均不表述为实测 |
| SLO 目标（5 个） | `REPO_VERIFIED`（文档） | `DESIGN_TARGET` | 在真实环境达成该目标 |
| Prometheus 告警（6 条） | `REPO_VERIFIED`（配置） | `PENDING`（生产触发） | 一个真实 Prometheus 实例加载并触发这些规则。阈值本身是 `DESIGN_TARGET` |
| Grafana 仪表盘（10 面板） | `REPO_VERIFIED`（JSON） | `PENDING` | 导入运行中的 Grafana 并确认面板被真实数据填充 |
| OTLP exporter | `REPO_VERIFIED`（实现） | `PENDING`（运行期闭环） | 应用 → exporter → collector → 后端 → 真的查到 span |
| RAGAS 质量分 | `REPO_VERIFIED`（harness） | `PENDING` | 获批 evaluator provider + API key + 一次真实运行 |
| BGE / CLIP / PaddleOCR 真实模型 | `REPO_VERIFIED`（adapter 契约） | `PENDING` | 真实权重与运行时的 smoke（当前 `EXTERNAL_MODEL_ASSET_REQUIRED`） |
| 4B / 14B vLLM GPU 拓扑 | `REPO_VERIFIED`（路由契约） | `PENDING` | 真实 GPU 部署与压测（权重不在仓库，`vllm` 未安装） |
| QLoRA 微调 | `REPO_VERIFIED`（工具） | `PENDING` | 可复现训练运行 + adapter 产物 |
| Airflow 调度 | `REPO_VERIFIED`（DAG 注册） | `PENDING` | 真实 Airflow DAG 执行 |
| Qdrant 真实服务 | `REPO_VERIFIED`（当前回归覆盖 = 进程内 `QdrantClient(":memory:")`） | `PENDING`（真实服务 artifact） | 一次新的真实服务运行并提交产物。**开发沿革中确有 PR #6/#7 的真实本地 Qdrant + ES 集成运行记录，但那不是可复现 artifact**——两个方向都不能说错，详见 [Qdrant evidence: two states, kept apart](docs/interview-evidence-map.md#qdrant-evidence-two-states-kept-apart) |
| 微服务（`api-gateway/` 等） | `REPO_VERIFIED`（组件） | `PENDING`（集成部署） | 与当前前端的端到端生产验证 |
| 前端 CI 构建（`frontend-build`：lockfile 安装 + `npm run build`） | `REPO_VERIFIED` | —（构建产物不发布） | 无需升级：这是门禁，不是结果。**但构建成功只证明 bundle 能编译** |
| 前端 + 真实后端端到端运行 | `REPO_VERIFIED`（客户端与 API metadata 契约） | `PENDING` | 一次真实浏览器运行：`frontend/` 对真实单体 + 真实 ES/Qdrant + 真实模型，并提交可复现 artifact。演示截图用 Playwright 驱动**真实 UI**，但后端是 `docs/demo/mock_api.py` 这个合成 mock，因此**不算**端到端证据 |
| 前端生产部署 | —（部署态在本仓库之外） | `PENDING` | 一次真实部署并记录环境。这是 deployment-specific 状态，本仓库不断言 |

### `LOCAL_REAL_VALIDATION` 完整清单（只有这五项）

本地单主机 + 真实依赖上实际跑过，证据见 [docs/validation/v2.5-runtime-security-validation.md](docs/validation/v2.5-runtime-security-validation.md)：

1. Redis 7.4.9 多进程会话持久化（进程 A 写入 → 进程 B 类型化恢复 → 进程 C 观察到更新 → TTL 刷新）
2. Redis 跨进程登录限流（跨两进程交替第 6 次 429、窗口过期、Redis 挂掉时降级单进程内存）
3. 真实 nginx 单跳与多跳 `TRUSTED_PROXIES` 客户端 IP 解析
4. 认证 Elasticsearch 8.11（匿名/错误凭据 401、writer mapping + `search_after`、在线 BM25 检索）
5. 带 Bearer token 的 Prometheus 抓取（无 token 401、Bearer 200、target `up == 1`）

**这不等于生产集群验证**：Redis Cluster/Sentinel、云负载均衡拓扑、多节点 ES/TLS、长期 Prometheus/Grafana 运维、生产 HA/SLO，以及 4B/14B vLLM GPU 部署，均未在本仓库验证。

### 指标与告警的契约

- 排障时引用的 `rag_*` 指标名必须真实存在：exporter 只暴露原始计数器（例如 `rag_cache_hit_L1`、`rag_cache_total`），**没有** `rag_cache_hit_rate` 或 `rag_rewrite_fallback_rate` 这类 series；比率要么用基于已 emit counter 的 PromQL ratio，要么读 `/api/stats` 的计算字段。`scripts/check_repo_consistency.py` 强制这条契约。
- **本仓库唯一的告警契约**是 [`monitoring/prometheus/alerts.yml`](monitoring/prometheus/alerts.yml)，由外部 Prometheus 加载评估。`monitoring/otel_tracer.py` 里还有一个更早的进程内 `AlertingManager`，**没有**接入 canonical 请求路径，属于遗留代码，喂给它的 `config.json` → `alerting.rules` 配置块也已移除。判定依据见 [audit](docs/repository-truth-audit.md#two-alerting-mechanisms-and-which-one-is-canonical)。
- `/api/stats` 与 `/api/metrics` 需要身份认证（`require_identity`）；`/api/health` 公开。Prometheus 抓取需配置 Bearer token。

---

## Documentation

请从 [docs/README.md](docs/README.md) 查找当前操作指南、设计文档与历史计划（历史计划不代表当前实现状态）。面试 / 真实性口径入口：

| 文档 | 作用 |
|---|---|
| [docs/interview-walkthrough.md](docs/interview-walkthrough.md) | **5 分钟 screen-share 脚本**：按面试问题分节，每节给出要说的话与要打开的代码文件 |
| [docs/interview-architecture-baseline.md](docs/interview-architecture-baseline.md) | **架构唯一事实基线**：召回路数、重排、Gate、路由、版本语义 |
| [docs/interview-evidence-map.md](docs/interview-evidence-map.md) | **证据等级唯一权威表** + 逐能力分级 + benchmark artifact 验收标准 + 已知表述风险 |
| [docs/repository-truth-audit.md](docs/repository-truth-audit.md) | 逐能力实现 / 证据 / 状态审计，含 Qdrant 两态、告警双机制、外部验证边界 |
| [docs/repository-metadata.md](docs/repository-metadata.md) | 期望的 GitHub 仓库 About 配置（description / topics / homepage / social preview） |
| [docs/security-regression-coverage.md](docs/security-regression-coverage.md) | 固定威胁清单、每项控制与测试、以及仍然开放的有界缺口 |
| [docs/slo-runbook.md](docs/slo-runbook.md) | 5 个 SLO 目标（均为 `DESIGN_TARGET`）+ 8 个按真实降级路径写的处置流程 |
| [docs/demo/README.md](docs/demo/README.md) | 首屏那张合成数据演示图的生成方式与复现命令 |
| [PRD.md](PRD.md) | 产品与架构设计；顶部 runtime reconciliation 表区分当前实现与历史/目标设计 |

**README 只保留摘要。** 详细的 caveat、逐项证据、边界条件与"不能怎么说"清单都在上表文档里，不在 README 重复。

---

## Repository Layout

```text
app.py                    FastAPI monolith entrypoint (canonical)
api/                      /api/* routes
core/                     Online RAG pipeline
retrieval/                Recall, RRF, BiEncoder, CrossEncoder, Evidence/Answer Gate
models/                   Model clients, prompt trust boundary, AdapterManager
router/                   Stateless 4B/14B routing
auth/  common/            RS256 auth, config, RBAC, structured audit
cache/  admission/        L1/L2 cache with permission partitioning; admission control
monitoring/               MetricsCollector, OTLP exporter, Prometheus rules, Grafana JSON
offline/                  Offline ingestion pipeline, embeddings, snapshot/epoch lifecycle
dags/                     Airflow DAG definitions (registration only)
benchmarks/               Deterministic retrieval + performance artifact frameworks
frontend/                 React application
api-gateway/  retrieval-service/  generation-service/  monitoring-service/
                           Microservice components; separate integration status
tests/                    Deterministic unit/integration/contract/performance suites
docs/                     User, operator, design, audit and interview-truth documentation
scripts/                  check_repo_consistency.py and validation helpers
config.json               Runtime configuration; system.version is 2.3.0
```

---

## License

[MIT](LICENSE)