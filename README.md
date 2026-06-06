# 化妆品企业级多模态 RAG 智能问答系统

面向中小型化妆品企业的智能知识问答系统，支持成分查询、法规咨询、配方研发、产品信息等多场景问答。

## 系统架构

基于 RAG（检索增强生成）管线，支持：

- 多模态输入（文本 + 图片）
- 双阶段检索（BiEncoder + CrossEncoder）
- 证据投票机制（Evidence Gate）
- NLI 答案校验（Answer Gate）
- 细粒度 RBAC 权限控制（JWT + Bitmask）
- L1 内存 / L2 Redis 双层缓存
- OpenTelemetry 分布式追踪 + Prometheus 指标
- 多级降级策略（Rewrite / Complexity / NLI / Cache）

### 部署模式

| 模式 | 说明 | 配置 |
|------|------|------|
| **Monolith** | 单进程全量部署 | `python app.py` |
| **Microservices** | 6 个独立服务 | `docker compose -f docker-compose.microservices.yml up` |

微服务拆分：`api-gateway` / `rewrite-service` / `retrieval-service` / `generation-service` / `cache-service` / `monitoring-service`

## 快速开始

### 前置依赖

- Docker & Docker Compose
- NVIDIA GPU + nvidia-container-toolkit（推理加速）

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，设置以下必填项：
#   JWT_SECRET          — JWT 签名密钥（≥32 字符）
#   REDIS_PASSWORD      — Redis 密码
#   MINIO_ACCESS_KEY    — MinIO 访问密钥
#   MINIO_SECRET_KEY    — MinIO 密钥
#   ELASTICSEARCH_PASSWORD — Elasticsearch 密码
#   SERVICE_AUTH_TOKEN  — 微服务间认证令牌
```

> **安全提示**：所有基础设施凭据为必填项，未配置时 Docker Compose 将拒绝启动。

### 2. 下载模型权重

```bash
# Qwen3-14B-Instruct / Qwen3-4B-Instruct
# bge-base-zh-v1.5
# clip-vit-base-patch16
# 其他模型见 models/ 目录
```

### 3. 启动服务

```bash
docker compose up -d
```

### 4. 初始化知识库

```bash
python -m offline.scheduler
```

### 5. 访问 API

- API 文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/api/health
- 指标端点: http://localhost:8000/api/metrics

## 认证与授权

系统使用 **JWT + RBAC Bitmask** 进行身份认证和权限控制。

### 获取 Token

```bash
# 登录获取 access_token
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "your_password"}'

# 响应示例：
# {
#   "access_token": "eyJ...",
#   "refresh_token": "eyJ...",
#   "token_type": "Bearer",
#   "expires_in": 900
# }
```

### 使用 Token 访问 API

```bash
# 单轮问答（需要 Bearer Token）
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{"query": "烟酰胺的安全浓度是多少？"}"

# 带会话的多轮对话
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{"message": "配方开发相关问题", "session_id": "sess_001"}'

# 刷新 Token
curl -X POST http://localhost:8000/api/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "<refresh_token>"}'
```

### 用户管理（Admin）

```bash
# 列出用户
curl http://localhost:8000/api/auth/users \
  -H "Authorization: Bearer <admin_token>"

# 创建用户
curl -X POST http://localhost:8000/api/auth/users \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user_001",
    "username": "researcher",
    "password": "SecurePass123",
    "display_name": "研发工程师",
    "roles": ["rd"],
    "departments": ["rd_dept"]
  }'
```

### RBAC 角色

| 角色 | Mask | 说明 |
|------|------|------|
| `admin` | `0x7FFFFFFF` | 超级管理员，可访问所有文档 |
| `rd` | `0x01` | 研发部门 |
| `quality` | `0x02` | 质量管理 |
| `regulation` | `0x04` | 法规合规 |
| `sales` | `0x08` | 销售部门 |

## API 端点

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/api/query` | 单轮 RAG 查询 | JWT |
| POST | `/api/chat` | 多轮对话 | JWT |
| WS | `/api/ws/chat/{session_id}` | WebSocket 对话 | JWT (query param) |
| POST | `/api/rewrite` | 独立 Rewrite 端点 | JWT |
| POST | `/api/generate` | 独立 Generate 端点 | JWT |
| GET | `/api/media/{doc_id}` | 文档媒体预签名 URL | JWT |
| GET | `/api/health` | 健康检查 | 无 |
| GET | `/api/stats` | 系统统计指标 | 无 |
| GET | `/api/metrics` | Prometheus 指标 | 无 |
| POST | `/api/auth/login` | 用户登录 | 无 |
| POST | `/api/auth/refresh` | 刷新 Token | 无 |
| GET | `/api/auth/users` | 列出用户 | JWT (admin) |
| POST | `/api/auth/users` | 创建用户 | JWT (admin) |
| PUT | `/api/auth/users/{id}/roles` | 更新角色 | JWT (admin) |

## 目录结构

```
.
├── api/                  # FastAPI 路由层 + 依赖注入
├── auth/                 # JWT 认证 + 用户存储 + RBAC
├── common/               # 共享模块（config, auth, audit, models）
├── core/                 # 管线编排器（OnlineRAGPipeline）
├── rewrite/              # Query Rewrite + 反馈
├── admission/            # KV 准入控制
├── retrieval/            # 检索模块（Dense/BM25/CLIP/Rerank）
├── models/               # 模型封装（Embedding/LLM/NLI/BLIP）
├── cache/                # L1 内存 / L2 Redis 缓存
├── router/               # 无状态路由
├── monitoring/           # OpenTelemetry + MetricsCollector
├── monitoring-service/   # 告警管理 + 指标采集
├── offline/              # 离线知识库构建
├── api-gateway/          # 微服务：API 网关
├── rewrite-service/      # 微服务：Query Rewrite
├── retrieval-service/    # 微服务：检索
├── generation-service/   # 微服务：生成
├── cache-service/        # 微服务：缓存
├── nginx/                # Nginx 反向代理配置
├── deploy/               # Prometheus 等部署配置
├── tests/                # 测试（494 项）
├── config.json           # 运行时配置
├── app.py                # FastAPI 入口（Monolith 模式）
├── run_services.py       # 微服务启动器
├── docker-compose.yml    # 生产部署配置
└── requirements.txt      # Python 依赖
```

## 安全特性

| 特性 | 说明 |
|------|------|
| **JWT 认证** | 所有 API 端点强制 Bearer Token 验证 |
| **RBAC Bitmask** | 基于位掩码的细粒度角色 + 部门权限控制 |
| **密码哈希** | bcrypt（SHA-256 回退兼容旧数据） |
| **密码策略** | 最少 8 字符，最多 128 字符 |
| **登录限流** | 5 次/分钟/IP，防止暴力破解 |
| **时序安全** | 服务间认证使用 `hmac.compare_digest` 防时序攻击 |
| **反枚举** | 不存在用户执行 dummy bcrypt，防止用户名枚举 |
| **审计日志** | 所有管理操作记录审计日志 |
| **Docker 加固** | 基础设施端口不暴露宿主机，凭据强制配置 |
| **Nginx 安全头** | HSTS / CSP / X-Content-Type-Options |
| **输入校验** | doc_id 正则防 Milvus 注入，MIME 类型白名单 |

## 开发

```bash
# 安装依赖
pip install -r requirements.txt

# 运行全量测试（494 项）
pytest tests/ -v

# 运行认证测试
pytest tests/test_auth_routes.py -v

# 本地启动（Monolith 模式）
python app.py

# 本地启动（微服务模式）
python run_services.py
```

## 性能指标

- P95 延迟：1.5~3.0s
- 有效并发：20~25
- QPS：12~18
- 多轮对话：最近 6 轮
- 测试覆盖率：494 项测试全部通过

## 监控

- **Prometheus 指标**：`GET /api/metrics`（无需认证）
- **健康检查**：`GET /api/health`（Redis / Milvus / ES 连通性）
- **系统统计**：`GET /api/stats`（缓存命中率、延迟百分位、KV 压力）
- **分布式追踪**：OpenTelemetry + Jaeger（可选）
- **告警**：可配置规则 + Webhook / Slack / Email 通知
