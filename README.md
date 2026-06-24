# 化妆品行业 RAG 问答系统

面向化妆品行业知识库的 RAG 问答系统。当前仓库里，已验证的主线是 `FastAPI 单体后端 + React 前端`；同时保留了一套微服务目录，但它不是当前前端默认联调目标。

## 当前已验证主线

- 后端入口：`python3 app.py`
- 前端入口：`frontend/`，构建后产物位于 `frontend/dist`
- 页面能力：登录、Token 刷新、单轮查询、多轮对话、会话历史查看、系统统计查看、证据文件打开、管理员用户创建、管理员角色/部门更新
- 核心接口：`/api/chat`、`/api/media/{doc_id}`、`/api/auth/*`、`/api/health`、`/api/stats`、`/api/metrics`
- 健康检查：`GET /api/health`

## 当前未闭环的上线阻塞

- 离线建库管线 (`offline/` 包) 已实现（含文档处理、图像OCR、向量化、调度、反馈闭环），但缺少 PaddleOCR、CLIP 等模型权重文件。首次部署需先下载模型权重或切换至已有外部数据集。
- 单体后端与微服务目录并存；如果要走微服务部署，需要单独补一轮契约校验，不应默认视为与当前前端完全一致。
- 默认运行安全策略依赖环境变量覆盖：生产环境必须显式设置 `AUTH_DEV_MODE=false`、JWT 密钥、CORS、Redis/MinIO 等密钥。

---

## 快速启动

### 1. 配置环境变量

```bash
cp .env.example .env
```

`common/config.py` 现在会自动加载项目根目录下的 `.env`，因此 `python3 app.py`、`pytest` 和 `python3 run_offline.py` 都会直接读取这份文件。

至少确认这些配置：

- `DEPLOYMENT_MODE=development|testing|production`
- `AUTH_DEV_MODE=false|true`
- `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH`
- `REDIS_PASSWORD`
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`
- `SERVICE_AUTH_TOKEN`
- `CORS_ORIGINS`（生产环境必须设置）

`common/config.py` 现在会优先读取 `.env` / 进程环境里的 `DEPLOYMENT_MODE` 和 `AUTH_DEV_MODE`，避免 `.env` 与 `config.json` 脱节。

### 2. 前端构建

```bash
cd frontend
npm install
npm run build
cd ..
```

构建完成后，`app.py` 会优先挂载 `frontend/dist`。

### 3. 本地单体启动

```bash
python3 app.py
```

访问地址：

- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/api/health
- 统计指标：http://localhost:8000/api/stats
- Prometheus 指标：http://localhost:8000/api/metrics
- 前端运行时元数据：http://localhost:8000/api/auth/metadata

### 4. Docker Compose 启动

```bash
docker compose up -d
```

`Dockerfile` 当前健康检查路径为 `/api/health`，与应用真实路由一致。

### 5. 知识库导入现状

当前仓库 `run_offline.py` 是预期入口，`offline/` 包已包含完整实现（文档处理、图像OCR、向量化、调度增量更新、全量重建、反馈闭环）。
但缺少 PaddleOCR 模型和 CLIP/BLIP/BGE 等模型权重文件，首次部署需先下载模型权重：

```bash
python3 run_offline.py --mode create-index   # 创建 Qdrant Collection 与 ES 索引
python3 run_offline.py --mode incremental    # 增量处理 data/ 目录下文档
```

离线管线依赖以下模型权重，需提前下载至 `models/` 目录：
- `bge-base-zh-v1.5` (文本向量化)
- `clip-vit-base-patch16` (图像向量化)
- `PaddleOCR` (OCR 文字识别)
- BLIP (离线可选)
- QLoRA 微调权重（可选，`offline/finetune_qlora.py`）

---

## Auth & RBAC

The system uses **JWT + RBAC Bitmask** for authentication and authorization.

### 获取 Token

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your_password"}'
```

### RBAC Roles

Configurable in `config.json` under `rbac.roles` and `rbac.departments`.

Default roles (integer bitmask values):

| Role | Mask | Description |
|------|------|-------------|
| `admin` | `0x7FFFFFFF` | Super admin |
| `rd` | `0x01` | R&D |
| `quality` | `0x02` | Quality |
| `regulation` | `0x04` | Compliance |
| `sales` | `0x08` | Sales |

### API Endpoints

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| POST | `/api/query` | 单轮查询 API（前端已接入 `Single Query` 模式） | JWT |
| POST | `/api/chat` | 多轮对话 | JWT |
| POST | `/api/continuation` | 长文续写占位接口（当前仍为空桩） | JWT |
| GET | `/api/dialog_history` | 会话历史查询（前端 `Session` 面板已接入） | JWT |
| GET | `/api/media/{doc_id}` | Document presigned URL | JWT |
| GET | `/api/health` | Health check | None |
| GET | `/api/stats` | System statistics | None |
| GET | `/api/metrics` | Prometheus metrics | None |
| POST | `/api/auth/login` | Login | None |
| POST | `/api/auth/refresh` | Refresh token | None |
| GET | `/api/auth/metadata` | UI/auth/RBAC metadata for browser clients | None |
| GET | `/api/auth/users` | List users | Admin |
| POST | `/api/auth/users` | Create user | Admin |
| PUT | `/api/auth/users/{id}/roles` | Update roles | Admin |

---

## 前后端契约说明

- 前端默认通过 `GET /api/auth/metadata` 读取标题、副标题、角色选项、是否启用 JWT、是否必须登录、匿名开发身份。
- 前端支持切换 `Single Query`（`POST /api/query`）和 `Multi-turn Chat`（`POST /api/chat`）。
- 前端 `Session` 面板对应 `GET /api/dialog_history`，`Stats` 面板对应 `GET /api/stats`。
- 管理员面板现在已对齐后端：
  - `GET /api/auth/users`
  - `POST /api/auth/users`
  - `PUT /api/auth/users/{user_id}/roles`
- 证据文档不会再直接裸跳转 `/api/media/{doc_id}`，而是先按当前身份获取预签名 URL，再打开真实对象地址。
- 管理员判定改为读取配置中的 `admin` 角色掩码，不再把 `rd` 角色误判成管理员。

## Configuration Guide

All runtime configuration is in `config.json`. Key sections:

### `domain_keywords`
Domain-specific keywords for business-type classification during query rewrite fallback.

```json
"domain_keywords": {
    "regulation": ["compliance", "standard", "permit"],
    "development": ["formula", "R&D", "process"],
    "ingredient": ["component", "concentration", "efficacy"],
    "product": ["brand", "price", "skin type"],
    "visual": ["image", "package", "label", "photo"]
}
```

### `prompts`
Customize LLM system prompts. Leave empty (`""`) to use built-in defaults.

### `model_routing`
Map logical tiers to actual model endpoints. Supports multi-model deployments.

### `ui`
Frontend runtime metadata. The React client reads this through `GET /api/auth/metadata`, so deployments can rebrand the app and change visible role options without editing `frontend/src/App.jsx`.

See [`docs/open-source-hardcoding-audit.md`](docs/open-source-hardcoding-audit.md) for the current hardcoding audit, frontend/backend contract, and remaining open-source cleanup backlog.

---

## Security Features

| Feature | Description |
|---------|-------------|
| **JWT Auth** | All API endpoints enforce Bearer token validation |
| **RBAC Bitmask** | Fine-grained role + department bitmask permissions |
| **Password Hashing** | bcrypt (SHA-256 fallback for legacy migration) |
| **Password Policy** | Min 8 chars, max 128 chars |
| **Login Rate Limit** | 5 req/min/IP |
| **Timing Safety** | `hmac.compare_digest` for inter-service auth |
| **Anti-Enumeration** | Dummy bcrypt for non-existent users |
| **Audit Logging** | All admin operations logged |
| **Input Validation** | Injection prevention via `validate_doc_id()`, MIME whitelist |

---

## Monitoring

- **Prometheus Metrics**: `GET /api/metrics` (no auth)
- **Health Check**: `GET /api/health` (Redis/Qdrant/ES connectivity)
- **System Stats**: `GET /api/stats` (cache hit rate, latency percentiles, KV pressure)
- **Distributed Tracing**: OpenTelemetry + Jaeger (optional)
- **Alerting**: Configurable rules + Webhook/Slack/Email notifications

---

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run full test suite
pytest tests/ -v

# Local monolith mode
python3 app.py

# Local microservices mode (需单独验证，不是当前前端默认联调主线)
python3 run_services.py
```

---

## Directory Structure

```
.
├── api/                  # FastAPI routing + dependency injection
├── auth/                 # JWT auth + user store + RBAC
├── common/               # Shared modules (config, auth, audit, models)
├── core/                 # Pipeline orchestrator (OnlineRAGPipeline)
├── rewrite/              # Query rewrite + feedback
├── admission/            # KV admission control
├── retrieval/            # Retrieval modules (Dense/BM25/CLIP/Rerank)
├── models/               # Model wrappers (Embedding/LLM/NLI/BLIP)
├── cache/                # L1 memory / L2 Redis cache
├── router/               # Stateless request router
├── monitoring/           # OpenTelemetry + MetricsCollector
├── offline/              # Offline pipeline: doc processing, OCR, vectorization, feedback loop, scheduling
├── monitoring-service/   # Alert management + metrics collection
├── dags/                 # Airflow DAG scripts (knowledge base refresh)
├── data/                 # Mock data, eval set generation, text mapping
├── run_offline.py        # Offline pipeline entry (incremental/full/create-index/feedback modes)
├── api-gateway/          # Microservice: API Gateway
├── rewrite-service/      # Microservice: Query Rewrite
├── retrieval-service/    # Microservice: Retrieval
├── generation-service/   # Microservice: Generation
├── cache-service/        # Microservice: Cache
├── frontend/             # React SPA frontend
├── nginx/                # Nginx reverse proxy config
├── deploy/               # Prometheus, Grafana configs
├── tests/                # Test suite
├── config.json           # Runtime configuration
├── app.py                # FastAPI entry (Monolith mode)
├── docker-compose.yml    # Production deployment
├── docker-compose.microservices.yml  # Microservices deployment
└── requirements.txt      # Python dependencies
```

---

## License

MIT
