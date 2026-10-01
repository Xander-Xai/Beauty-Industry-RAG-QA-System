# 化妆品行业 RAG 问答系统部署手册

## 1. 当前推荐部署路径

当前仓库推荐先走 `app.py` 单体部署，再逐步补微服务。原因：

- React 前端默认联调的是单体 `/api/*` 接口
- `Dockerfile`、`docker-compose.yml`、`/api/health` 已对齐
- 微服务目录仍在，但不是当前验证过的前端主线

## 2. 部署前必须确认

### 2.1 环境变量

```bash
cp .env.example .env
```

`common/config.py` 负责读取应用配置。按 `.env.example` 准备环境；运行单体应用使用 `python3 app.py`。离线知识库由 `run_offline.py` 子命令构建（`create-index` / `ingest` / `incremental-build` / `full-rebuild` / `seal-epoch`），操作细节见 [数据管理手册](data-admin-guide.md)。

生产环境至少明确设置：

- `DEPLOYMENT_MODE=production`
- `AUTH_DEV_MODE=false`
- `CORS_ORIGINS=https://your-frontend.example.com`
- `JWT_PRIVATE_KEY_PATH`
- `JWT_PUBLIC_KEY_PATH`
- `JWT_ALGORITHM=RS256`
- `REDIS_PASSWORD`（Compose fail-fast：未设置时 Redis/ app 启动失败）
- `ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD`（Compose 启用 `xpack.security.enabled=true`，缺失时 fail-fast）
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `SERVICE_AUTH_TOKEN`
- `TRUSTED_PROXIES`（可选：仅当应用在可信反向代理之后、需要按真实客户端 IP 限流时设置）

说明：

- `common/config.py` 会优先读取 `.env` / 进程环境中的 `DEPLOYMENT_MODE` 和 `AUTH_DEV_MODE`。
- 如果生产环境仍保留 `AUTH_DEV_MODE=true`，任意客户端都可伪造 `X-User-*` 头部，不符合真实上线要求。
- Elasticsearch 安全边界：Compose 使用 `elastic` 用户 + `ELASTICSEARCH_PASSWORD`，健康检查也走认证请求；在线 `retrieval/bm25_retriever.py` 与离线 writer 会优先读取环境凭据，回退到 `config.json`。不要把无认证 ES 当作生产默认配置。
- `GET /api/stats` 与 `GET /api/metrics` 需要身份认证（`require_identity`）；`GET /api/health` 保持公开。Prometheus 抓取需配置 `Authorization: Bearer <token>`。
- 登录限流仅在 TCP 对端属于 `TRUSTED_PROXIES` 时才解析 `X-Forwarded-For`；未配置时使用对端地址，客户端无法通过伪造 XFF 绕过限流。
- 认证算法边界：浏览器登录由 `POST /api/auth/login` 签发 RS256 access/refresh token，`common/auth.parse_identity` 以 RS256 验签为主。RS256 验签不依赖 legacy `JWT_SECRET`；`JWT_SECRET`（HS256）仅为可选向后兼容回退，只有需要继续接受旧 HS256 token 时才设置。生产环境只需 `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` / `JWT_ALGORITHM`。

### 2.2 前端构建

```bash
cd frontend
npm install
npm run build
cd ..
```

构建完成后，FastAPI 会从 `frontend/dist` 提供静态页面。

### 2.3 JWT 密钥

```bash
mkdir -p keys
python3 -c "from auth.jwt_auth import generate_keypair; generate_keypair('./keys')"
```

## 3. 启动方式

### 3.1 本地单体

```bash
python3 app.py
```

### 3.2 Compose

```bash
docker compose up -d
```

校验点：

- `http://localhost:8000/api/health`（公开）
- `http://localhost:8000/docs`（生产模式下禁用）
- `http://localhost:8000/api/auth/metadata`
- `http://localhost:8000/`

Compose 需要 `REDIS_PASSWORD`、`ELASTICSEARCH_PASSWORD`、`MINIO_*`、`SERVICE_AUTH_TOKEN`；缺失时 compose 会直接报错退出（fail-fast，这是有意设计，不是 bug）。

在线会话（`SessionState`）与登录限流在配置了 Redis 时跨 worker 共享，Redis 不可用时降级为进程内内存（此时不跨 worker）。

## 4. 离线知识库部署要求

离线管线代码位于 `offline/`，入口为 `run_offline.py`。部署时需要区分：

- **默认应用部署**：`python3 app.py` 或 `docker compose up -d`，提供在线问答；不包含 Airflow，
  也不自动运行离线构建。
- **离线 ingestion 依赖**：PDF/DOCX/XLSX 解析库已包含在 `requirements.txt`
  （PyMuPDF / python-docx / openpyxl / pandas）。
- **可选 OCR 运行时**：处理扫描件或图片时安装 `offline/requirements-ocr.txt`
  （PaddleOCR / PaddlePaddle）。默认不安装，CI 不依赖它。
- **可选 Airflow 部署**：`dags/knowledge_base_dags.py` 仅在 Airflow 已安装且离线模块可发现时
  注册 DAG。默认 `docker compose` 不启动 Airflow；DAG 代码存在不等于调度器在运行。
- **外部模型资产**：真实 BGE/CLIP 权重需由操作者按 `config.json` 准备；仓库不随附，也不在
  import 阶段下载。缺少资产时真实模型 smoke 状态为 `EXTERNAL_MODEL_ASSET_REQUIRED`。

离线构建、校验与封存命令见 [数据管理手册](data-admin-guide.md)。构建出的 epoch 需要操作者
手动切换 `config.json` 的 `knowledge_version_epoch` 才会对在线检索生效。

## 5. 当前已对齐的前后端能力

- 登录：`POST /api/auth/login`
- 刷新 Token：`POST /api/auth/refresh`
- 单轮查询：`POST /api/query`
- 多轮问答：`POST /api/chat`
- 会话历史：`GET /api/dialog_history`
- 系统统计：`GET /api/stats`（需要认证）
- Prometheus 指标：`GET /api/metrics`（需要认证）
- 健康检查：`GET /api/health`（公开）
- 证据文档打开：`GET /api/media/{doc_id}`，由前端先取预签名链接再打开
- 管理员用户管理：
  - `GET /api/auth/users`
  - `POST /api/auth/users`
  - `PUT /api/auth/users/{user_id}/roles`

## 6. 生产前额外检查

- 将 `AUTH_DEV_MODE` 设为 `false`
- 确认 `.env` 已被当前启动进程加载
- 配置 `CORS_ORIGINS`
- 配置 `ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD`，确认 ES 已启用认证
- 如需反向代理限流，配置 `TRUSTED_PROXIES`（且代理确实追加 XFF）
- 为 Prometheus/监控配置 `GET /api/metrics` 的 Bearer token
- 构建前端并确认 `frontend/dist` 已生成
- 使用管理员账号验证用户创建、角色更新、证据文件访问
- 确认 `Dockerfile` 健康检查访问的是 `/api/health`
- 审核 `docs/pre-launch-checklist.md` 中的 P0 阻塞项
