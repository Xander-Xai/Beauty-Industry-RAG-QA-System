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

应用现在会自动加载项目根目录下的 `.env`，所以直接执行 `python3 app.py` 或 `python3 run_offline.py` 即可读取这份配置。

生产环境至少明确设置：

- `DEPLOYMENT_MODE=production`
- `AUTH_DEV_MODE=false`
- `CORS_ORIGINS=https://your-frontend.example.com`
- `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH`（RS256 密钥对路径）
- `ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD`（ES xpack 凭据）
- `REDIS_PASSWORD`
- `MINIO_ACCESS_KEY`
- `MINIO_SECRET_KEY`
- `SERVICE_AUTH_TOKEN`

说明：

- `config.json` 中 `auth.dev_mode` 现在**默认关闭**（`false`），需要开发模式时通过环境变量 `AUTH_DEV_MODE=true` 显式启用。
- `app.py` 在生产模式（`DEPLOYMENT_MODE=production`）下会自动禁用 uvicorn 热重载。
- `CORS_ORIGINS` 在生产模式下为强制项，缺失时服务将硬性阻止启动（v2.5.0）。

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

生产环境建议使用 OpenSSL 生成更安全的密钥：

```bash
mkdir -p keys
openssl genrsa -out keys/private.pem 2048
openssl rsa -in keys/private.pem -pubout -out keys/public.pem
chmod 600 keys/private.pem
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
- `http://localhost:8000/api/auth/metadata`
- `http://localhost:8000/`

> **注意**：`/api/stats` 和 `/api/metrics` 现在需要 JWT 认证（v2.5.0），无法直接浏览器访问，需通过 `curl -H "Authorization: Bearer <token>"` 或 Grafana 等监控工具配置认证后访问。

## 4. 知识库初始化现状

当前仓库 `offline/` 包已包含完整离线管线实现（文档处理、图像 OCR、向量化、调度），`run_offline.py` 可直接执行增量更新/全量重建/创建索引等操作。

首次部署时，根据数据集情况选择：

1. **使用外部已有数据**：确认 `config.json` 中 Qdrant/ES 集合名与现网一致，直接启动在线服务。
2. **使用本仓库离线管线**：先下载模型权重至 `models/` 目录（PaddleOCR、CLIP-ViT、BGE base zh v1.5 等），然后执行：
   ```bash
   python3 run_offline.py --mode create-index   # 创建 Qdrant Collection + ES 索引
   python3 run_offline.py --mode incremental    # 导入 data/ 目录文档
   ```

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
- 确认 JWT RS256 密钥对已生成（`keys/private.pem`、`keys/public.pem`）
- 确认 `ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD` 已配置（v2.5.0 ES xpack 安全加固）
- 配置 `CORS_ORIGINS`（生产模式强制项）
- 构建前端并确认 `frontend/dist` 已生成
- 使用管理员账号验证用户创建、角色更新、证据文件访问
- 确认 `Dockerfile` 健康检查访问的是 `/api/health`
- 确认 Redis 会话可以跨多 worker 共享（v2.5.0：`SessionState` 已支持 Redis 持久化，TTL=7200s）
- 审核 `docs/pre-launch-checklist.md` 中的 P0 阻塞项
- 确认 `docs/ragas-evaluation-guide.md` 中的 RAGAS 评估配置（可选，仅用于离线质量评估）
