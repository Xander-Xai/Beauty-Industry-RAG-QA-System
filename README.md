# 化妆品行业 RAG 问答系统

面向化妆品行业知识库的 FastAPI + React RAG 问答项目。README 描述仓库当前状态；设计目标与实现差距见 [PRD](PRD.md) 和 [Repository Truth Audit](docs/repository-truth-audit.md)。

## 当前应用入口

- **Canonical application:** `app.py`，FastAPI 单体应用，提供 `/api/*` 路由；React 前端位于 `frontend/`。
- 仓库同时保留 `api-gateway/`、`retrieval-service/`、`generation-service/`、`monitoring-service/` 等微服务目录。它们是代码组件，不代表已完成与当前前端的端到端生产验证。当前默认主线是单体应用。
- 后端查询、改写、检索、重排、证据门控和生成代码位于 `core/`、`rewrite/`、`retrieval/`、`models/`。

## 离线处理状态

当前 `offline/` 仅包含 QLoRA 微调脚本、样本数据和微调依赖文件。

**已包含：** QLoRA fine-tuning utility（`offline/finetune_qlora.py`）、fine-tuning sample data、独立依赖清单。

**当前不包含：** 文档导入与解析、OCR 导入、向量生成和写入、增量调度、索引创建/重建、反馈闭环。`run_offline.py` 对 ingestion 模式会明确报错。相关设计和验收范围跟踪于 [Issue #2](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/2)。

仓库中存在 Airflow DAG 草案；它引用的 scheduler/feedback modules 不存在，因此当前不会注册可用的 ingestion DAG。DAG 文件存在不等同于生产导入管线。需要导入数据时，请连接已准备好的外部 Qdrant/Elasticsearch 索引；本仓库目前没有从原始文档创建该索引的已验证命令。

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
offline/                  QLoRA utility, sample data and dependencies only
run_offline.py            Explicitly rejects unavailable ingestion modes
frontend/                 React application
api-gateway/              Microservice code; separate integration status
retrieval-service/        Microservice code; separate integration status
generation-service/       Microservice code; separate integration status
monitoring-service/       Microservice code; separate integration status
tests/                    Runtime tests and non-collected planned contracts
docs/                     User, operator, design and audit documentation
config.json               Runtime configuration; system.version is 2.3.0
```

## License

[MIT](LICENSE)
