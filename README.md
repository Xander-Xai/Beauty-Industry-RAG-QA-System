# 化妆品行业 RAG 问答系统

面向化妆品行业知识库的 FastAPI + React RAG 问答项目。README 描述仓库当前状态；设计目标与实现差距见 [PRD](PRD.md) 和 [Repository Truth Audit](docs/repository-truth-audit.md)。

## 当前应用入口

- **Canonical application:** `app.py`，FastAPI 单体应用，提供 `/api/*` 路由；React 前端位于 `frontend/`。
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

## 当前代码能力

- FastAPI 单体入口及 `/api/query`、`/api/chat`、认证、会话、媒体访问和指标路由。
- 认证以 RS256 为主：`POST /api/auth/login` 签发 RS256 access/refresh token；`common/auth` 以 RS256 验签，旧 HS256 `JWT_SECRET` 仅为可选兼容回退。
- 多路召回及 Reciprocal Rank Fusion（RRF）实现。
- 可配置的 BiEncoder rerank 阶段及 CrossEncoder ensemble 代码。
- PEFT `AdapterManager` 与 `LLMClient` 集成；是否实际加载 adapter 取决于本地模型、依赖和配置。仓库没有随附训练后的 adapter 权重。
- QLoRA 微调脚本和小型样本数据；脚本存在不代表本仓库已验证训练结果。
- RAGAS evaluation harness source 与 golden set 存在；RAGAS 不在默认依赖中，等待上游修复当前已知安全问题后再启用安装。评测工具存在不代表模型质量或生产指标已达标。

这些能力的实现边界和证据列于 [audit](docs/repository-truth-audit.md)。性能数字如未附 benchmark 产物，不视为已验证结果。

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
