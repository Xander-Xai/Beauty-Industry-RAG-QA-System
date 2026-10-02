# 化妆品行业 RAG 问答系统

面向化妆品行业知识库的 FastAPI + React RAG 问答项目。README 描述仓库当前状态；设计目标与实现差距见 [PRD](PRD.md) 和 [Repository Truth Audit](docs/repository-truth-audit.md)。

## 当前应用入口

- **Canonical application:** `app.py`，FastAPI 单体应用（使用 `lifespan` 上下文管理器处理启动/关闭，替代已弃用的 `on_event`），提供 `/api/*` 路由；React 前端位于 `frontend/`。
- 仓库同时保留 `api-gateway/`、`retrieval-service/`、`generation-service/`、`monitoring-service/` 等微服务目录。它们是代码组件，不代表已完成与当前前端的端到端生产验证。当前默认主线是单体应用。
- 后端查询、改写、检索、重排、证据门控和生成代码位于 `core/`、`rewrite/`、`retrieval/`、`models/`。

## 离线处理状态

离线知识库管线位于 `offline/`，通过 `run_offline.py` 子命令发现文档、构建完整 epoch 快照、校验并封存。实现证据与测试证据分离列于 [Repository Truth Audit](docs/repository-truth-audit.md)，操作细节见 [数据管理手册](docs/data-admin-guide.md)。

### 已在代码与确定性测试中实现

- 多格式解析：UTF-8 TXT、PDF（文本页 + 扫描页 OCR 路由）、DOCX、XLSX，以及独立图片（PNG/JPEG/WebP/BMP/TIFF）。
- 确定性字符切块（默认 500 字符、10% 重叠）与稳定逻辑身份（`doc_id` / `chunk_id` / `image_id`）；物理 Qdrant point ID 按 epoch 版本化。
- BGE 文本 embedding adapter 与 CLIP 图像 embedding adapter（512d），共用在线查询的预处理与归一化契约。
- Qdrant 文本/图像 writer 与 Elasticsearch `cosmetics_docs` writer（显式 mapping，RBAC 字段为 keyword/long）。
- 增量状态检测（内容哈希为准）、snapshot carry-forward、全量重建、快照校验、epoch 封存。
- 调度抽象（cron/Airflow/CLI 共用同一业务逻辑）与反馈导出（`review_status` 门控）。
- CLI：`create-index`、`ingest`、`incremental-build`、`full-rebuild`、`seal-epoch`；`ingest-text` 作为向后兼容的 TXT 专用子命令保留。
- 跨平台文件锁（POSIX `fcntl` / Windows `msvcrt`）。

### 需要外部运行时 / 资产

- 真实 BGE 模型（`config.json` → `embedding.text.model_path`）。
- 真实 CLIP 模型（启用视觉检索时，`embedding.image_clip.model_path`）。
- PaddleOCR / PaddlePaddle（需要 OCR 时；见 `offline/requirements-ocr.txt`，默认不安装）。
- Airflow（仅在希望由 Airflow 调度时；默认 Compose 不启动 Airflow，DAG 代码存在不等于调度器在运行）。

### 尚未作为生产结果验证

- 真实模型质量、生产延迟/QPS、大规模语料吞吐。真实 BGE/CLIP/PaddleOCR smoke 需要本地模型资产；本仓库当前未执行，状态为 `EXTERNAL_MODEL_ASSET_REQUIRED`，不使用确定性测试 embedder 冒充真实模型验证。

### 常用离线命令

```bash
python3 run_offline.py create-index
python3 run_offline.py full-rebuild --epoch phase_2
python3 run_offline.py seal-epoch --epoch phase_2
python3 run_offline.py incremental-build --from-epoch phase_1 --to-epoch phase_2
```

`seal-epoch` 默认先做完整快照校验（Qdrant text/image + Elasticsearch）再封存；`--skip-validation` 是明确的危险逃生口。封存后需要操作者手动把 `config.json` 的 `knowledge_version_epoch` 切换到新 epoch 并重启在线服务。

## Quick Start

### 依赖与配置

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
```

运行时配置由根目录 `config.json` 和 `common/config.py` 管理；敏感值应通过环境变量提供。请先检查 `.env.example`，并在生产环境显式配置认证、CORS 和服务凭据。

### 启动 API

```bash
python3 app.py
```

默认地址为 `http://localhost:8000`。API 文档在 `/docs`，健康检查为 `/api/health`。前端源码在 `frontend/`；构建命令为：

```bash
cd frontend
npm ci
npm run build
```

### Docker Compose

```bash
docker compose up -d
```

Compose 需要 Redis、Qdrant、Elasticsearch 等服务。启动前检查 compose 文件和环境配置。

安全相关环境变量（Compose 在缺失时会 fail-fast，这是有意行为）：

- `REDIS_PASSWORD`：Redis 启用 `requirepass`。
- `ELASTICSEARCH_PASSWORD`：Elasticsearch 启用 `xpack.security.enabled=true`，客户端使用 `elastic` 用户 + 该密码（`ELASTICSEARCH_USERNAME` 默认 `elastic`）。
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`、`SERVICE_AUTH_TOKEN`。
- 生产环境还需 `CORS_ORIGINS`、JWT 密钥（`JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` / `JWT_ALGORITHM=RS256`）。
- 反向代理部署若需按真实客户端 IP 限流，显式设置 `TRUSTED_PROXIES`（逗号分隔的 IP/CIDR）；未设置时不信任 `X-Forwarded-For`。

## 当前代码能力

- FastAPI 单体入口及 `/api/query`、`/api/chat`、认证、会话、媒体访问和指标路由。
- 生成拓扑：**单一共享 4B vLLM 端点**（`config.json` → `gpu1.models.vllm_4b`，端口 8101）承担 Query Rewrite 与简单生成（endpoint 键 `gen_4b`）；复杂生成路由到 `gpu0.models.gen_14b`（Qwen3-14B）。详见 [PRD](PRD.md) 的 runtime reconciliation 表。
- 认证以 RS256 为主：`POST /api/auth/login` 签发 RS256 access/refresh token；`common/auth` 以 RS256 验签，旧 HS256 `JWT_SECRET` 仅为可选兼容回退。
- 会话状态 `SessionState` 支持 Redis 持久化（跨 worker，TTL 7200s）；Pydantic 对象经稳定 schema 序列化并在读取时重建；Redis 不可用时**静默降级为进程内内存**（此时不跨 worker 共享）。
- 登录限流 5 次/分钟：多 worker 走 Redis 计数，Redis 不可用时降级为单进程内存限流；仅当 TCP 对端属于 `TRUSTED_PROXIES` 时才解析 `X-Forwarded-For`，否则一律使用对端地址。
- `GET /api/stats` 与 `GET /api/metrics` 需要身份认证（`require_identity`）；`GET /api/health` 公开。Prometheus 抓取需配置 Bearer token。
- 多路召回及 Reciprocal Rank Fusion（RRF）实现；CLIP 同步路由阈值由 `config.json` → `clip_sync` 驱动。
- 可配置的 BiEncoder rerank 阶段及 CrossEncoder ensemble 代码。
- PEFT `AdapterManager` 与 `LLMClient` 集成；是否实际加载 adapter 取决于本地模型、依赖和配置。仓库没有随附训练后的 adapter 权重。
- QLoRA 微调脚本和小型样本数据；脚本存在不代表本仓库已验证训练结果。
- RAGAS evaluation harness / reporter / validator 与 golden set 存在（最初 seed 27 条，当前已扩展到 300+ 条；实际条目数以 `validate_golden_set` 输出和 `golden_set.jsonl` 为准）。RAGAS 是隔离的可选 evaluator，不在默认依赖中（等待上游安全修复）。库级 `evaluate()` 保留 evaluator-unavailable fallback，该结果不是质量结果；使用 `--require-ragas` 运行 strict / real evaluator CLI 时，缺少 evaluator dependency 或 evaluator credential 会 **fail fast** 并返回非零状态且不生成 quality report。当前仓库没有经过验证的真实 RAGAS quality score。

这些能力的实现边界和证据列于 [audit](docs/repository-truth-audit.md)。性能数字如未附 benchmark 产物，不视为已验证结果。

## Retrieval benchmark

`benchmarks/` 提供**确定性、可复现的 retrieval benchmark**：纯函数实现 Recall@1/3/5/10、HitRate@1/3/5/10、MRR@10、NDCG@10，自动产出 provenance 与 artifact，并支持 `overall` / `business_type` / `difficulty` 分桶。

```bash
# 查看当前环境实际可执行的配置（实时探测，不使用替身 retriever）
python -m benchmarks.retrieval_benchmark --list-configs

# 尝试真实运行
python -m benchmarks.retrieval_benchmark --config bm25 --limit 5
```

当前状态：benchmark 框架为 `REPO_VERIFIED`；**retrieval benchmark 结果为 `PENDING`**，仓库内没有可复现的真实 benchmark artifact。缺少真实依赖时，配置会以 `BLOCKED` 与原因记录，**不会**产出数字。数据质量缺口见 [docs/benchmark-data-quality.md](docs/benchmark-data-quality.md)，artifact 说明见 [artifacts/benchmarks/README.md](artifacts/benchmarks/README.md)。

## Enterprise operations / observability

除检索与生成质量外，本仓库补齐了运维证据闭环：性能证据产物契约、结构化审计、SLO 与故障 Runbook、Prometheus 告警规则、可选 OTLP 导出链路。实现与证据状态严格分层：

| 能力 | 状态 |
|---|---|
| performance artifact 框架（七文件契约、`BLOCKED`/`PARTIAL`/`EXECUTED`、未测量即 `null`） | `REPO_VERIFIED` |
| 结构化企业动作审计（统一 schema、强制脱敏、request_id 关联、Redis Stream + JSONL 持久化） | `REPO_VERIFIED` |
| SLO 与故障 Runbook（5 个目标 + 8 个故障处置流程） | `REPO_VERIFIED`（目标为 `DESIGN_TARGET`） |
| Prometheus 告警规则（6 条，全部基于真实 emit 的指标） | `REPO_VERIFIED` |
| Grafana 最小仪表盘（10 个面板，仅真实指标） | `REPO_VERIFIED` |
| OTLP exporter 实现（默认关闭、失败不影响业务、span 属性白名单） | `REPO_VERIFIED` |
| 真实性能产物 | `PENDING`（无 artifact） |
| 告警在生产触发 | `PENDING` |
| OTLP 运行时闭环（应用 → exporter → collector → 后端 → 查到 span） | `PENDING` |
| SLO 达成 | `PENDING`（全部为 `DESIGN_TARGET`） |

生产级依赖降级路径（Redis→进程内会话、ES→空结果走 dense、Qdrant→BM25-only、rewrite→简单档位）是代码中真实存在的实现，Runbook 按这些真实降级模式编写。

详见 [docs/slo-runbook.md](docs/slo-runbook.md)、[docs/interview-evidence-map.md](docs/interview-evidence-map.md) 与 [docs/repository-truth-audit.md](docs/repository-truth-audit.md)。

## Historical production context

作者此前公司生产环境的业务规模与流量背景（3000+ 文档、5000+ 图片、1500+ 产品、2000+ 成分、8 大法规体系、200+ 内部用户、高峰短时 10–15 QPS、日均 1500+ 请求；RTX A5000 ×2 生产推理环境，后续阶段完成 Qwen2.5 → Qwen3-14B / Qwen3-4B 灰度迁移验证）记录在 [docs/interview-evidence-map.md](docs/interview-evidence-map.md)，分类为 `HISTORICAL_PRODUCTION`。公开仓库不包含对应的专有语料、生产日志、模型权重或监控数据，因此这些**不是** `REPO_VERIFIED`，也不可由本仓库复现。

## 文档入口

请从 [docs/README.md](docs/README.md) 查找当前操作指南、设计文档和历史计划。历史计划不代表当前实现状态。

## 开发与检查

```bash
python3 -m pytest tests/ -v --tb=short
ruff check .
ruff format --check .
python3 scripts/check_repo_consistency.py
```

CI 工作流会运行测试和 Ruff；安全扫描是独立工作流，结果以 GitHub Actions 中的实际运行状态为准。

## 主要目录

```text
app.py                    FastAPI monolith entrypoint
api/                      /api/* routes
core/                     Online RAG pipeline
retrieval/                Retrieval, RRF, BiEncoder and reranking
models/                   Model clients and AdapterManager
offline/                  Offline ingestion pipeline and QLoRA utility
run_offline.py            Offline ingestion/rebuild/seal and rewrite-feedback entrypoint
frontend/                 React application
api-gateway/              Microservice code; separate integration status
retrieval-service/        Microservice code; separate integration status
generation-service/       Microservice code; separate integration status
monitoring-service/       Microservice code; separate integration status
tests/                    Runtime tests and non-collected historical contracts
docs/                     User, operator, design and audit documentation
config.json               Runtime configuration; system.version is 2.3.0
```

## License

[MIT](LICENSE)
