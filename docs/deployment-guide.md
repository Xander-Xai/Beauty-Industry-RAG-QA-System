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
- `REDIS_PASSWORD`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `SERVICE_AUTH_TOKEN`

说明：

- `common/config.py` 会优先读取 `.env` / 进程环境中的 `DEPLOYMENT_MODE` 和 `AUTH_DEV_MODE`。
- 如果生产环境仍保留 `AUTH_DEV_MODE=true`，任意客户端都可伪造 `X-User-*` 头部，不符合真实上线要求。

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

- `http://localhost:8000/api/health`
- `http://localhost:8000/docs`
- `http://localhost:8000/api/auth/metadata`
- `http://localhost:8000/`

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
- 系统统计：`GET /api/stats`
- 证据文档打开：`GET /api/media/{doc_id}`，由前端先取预签名链接再打开
- 管理员用户管理：
  - `GET /api/auth/users`
  - `POST /api/auth/users`
  - `PUT /api/auth/users/{user_id}/roles`

## 6. 生产前额外检查

- 将 `AUTH_DEV_MODE` 设为 `false`
- 确认 `.env` 已被当前启动进程加载
- 配置 `CORS_ORIGINS`
- 构建前端并确认 `frontend/dist` 已生成
- 使用管理员账号验证用户创建、角色更新、证据文件访问
- 确认 `Dockerfile` 健康检查访问的是 `/api/health`
- 审核 `docs/pre-launch-checklist.md` 中的 P0 阻塞项
