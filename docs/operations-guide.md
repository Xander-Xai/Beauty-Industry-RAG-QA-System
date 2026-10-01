# 化妆品行业 RAG 问答系统运维手册

## 1. 先明确当前运维范围

当前仓库已经能确认的在线运维对象是单体 FastAPI 应用：

- 入口：`app.py`
- 健康检查：`GET /api/health`（无认证，可用于负载均衡探测）
- 统计：`GET /api/stats`（需要 JWT 认证，v2.5.0）
- 指标：`GET /api/metrics`（需要 JWT 认证，v2.5.0）

> **注意**：从 v2.5.0 开始，`/api/stats` 和 `/api/metrics` 需要 Bearer Token 认证，Prometheus/Grafana 抓取需配置 HTTP Bearer 认证。

微服务目录仍在仓库中，但不应默认按“已完成整套线上运维验证”处理。

## 2. 当前值得盯的指标

### 应用可用性

- `/api/health`（无认证，标准健康检查）
- `/api/stats`（需 Bearer Token）
- `/api/metrics`（需 Bearer Token）
- `/api/auth/metadata`（无认证，前端运行时元数据）

### 监控集成

由于 `/api/stats` 和 `/api/metrics` 从 v2.5.0 开始需要 JWT 认证，Prometheus/Grafana 集成时需注意：

- **Prometheus scrape config**：增加 `Authorization: Bearer <token>` header
- **Grafana data source**：使用 `http_headers` 配置 `Authorization` 头
- 建议使用专用的服务账号 Token，带上 admin 角色掩码

### 认证与权限

- 登录成功率
- Token 刷新成功率
- `/api/auth/users` 管理接口返回码
- `/api/media/{doc_id}` 的 401 / 403 / 404 比例

### 依赖健康

- Redis 可连通性（支持密码认证，v2.5.0 起会话状态通过 Redis 持久化，TTL=7200s）
- Qdrant 可连通性
- Elasticsearch 可连通性（v2.5.0 起启用 xpack.security，需配置用户名/密码）
- MinIO 可用性

## 3. 当前已知运维风险

- **离线建库模型依赖**：离线管线代码已实现，但需额外下载 PaddleOCR、CLIP、BGE 等模型权重至 `models/` 目录后方可完整运行。
- **单体与微服务并存**：排障时必须先确认当前请求到底走的是 `app.py` 还是单独网关/微服务。
- **开发身份开关**：`config.json` 中 `auth.dev_mode` 现在**默认关闭**（`false`），`AUTH_DEV_MODE=true` 仅应在开发环境显式启用。生产环境误保留 `dev_mode=true` 会形成身份伪造风险。
- **运行配置来源**：服务现在会自动读取项目根目录 `.env`；排障时要同时检查 `.env` 与进程环境。
- **ES xpack.security**：v2.5.0 起 Elasticsearch 默认启用安全认证，需确保 `ELASTICSEARCH_USERNAME`/`ELASTICSEARCH_PASSWORD` 已配置且与 docker-compose 中的一致。
- **Redis 密码一致性**：`REDIS_CACHE_PASSWORD`、`REDIS_STATE_PASSWORD`、`REDIS_PASSWORD` 三个变量需保持一致，否则会话持久化（v2.5.0）和缓存功能会因连接失败而降级。
- **Stats/Metrics 认证**：v2.5.0 起 `/api/stats` 和 `/api/metrics` 需要 JWT 认证，监控工具需额外配置 Bearer Token。

## 4. 生产巡检建议

### 每次部署后

1. 访问 `GET /api/health`
2. 访问 `GET /api/auth/metadata`
3. 使用管理员账号登录
4. 验证管理员创建用户、更新角色/部门
5. 用不同角色验证证据文档访问权限
6. 打开首页，确认构建后的前端已被静态挂载
7. 打开 `Session` / `Stats` 面板，确认它们分别命中 `/api/dialog_history` 和 `/api/stats`
8. 确认 Elasticsearch 安全连接正常（`GET /api/health` 中 `elasticsearch` 依赖为 `true`）
9. 确认多 worker 部署下 Session 可以跨进程共享（相同 `session_id` 可被不同 worker 处理）

### 每日

1. 检查 Redis / Qdrant / Elasticsearch / MinIO 容器状态
2. 抽样检查 `/api/stats`（需 Bearer Token）
3. 检查错误日志中的 401 / 403 / 500
4. 检查 ES 认证状态（`curl -u elastic:$PASSWORD localhost:9200/_cluster/health`）

### 每周

1. 运行 RAGAS 黄金数据集评估追踪质量变化：`python -m tests.evaluation.ragas_eval --tag weekly-YYYY-MM-DD`
2. 对比本周与上周的评估报告：`python -m tests.evaluation.ragas_report --compare data/eval/reports/ragas_report_weekly-...`
3. 检查各业务类型（成分/法规/配方/图像/通用）的指标是否有退化
4. 审计 `docs/ragas-evaluation-guide.md` 以获取详细的评估配置说明

### 每月

1. 更新黄金数据集（添加新场景、边缘案例）
2. 运行全量 RAGAS 评估并更新基线
3. 检查检索质量（Context Precision / Recall）趋势

## 5. 常见故障定位

### 首页能访问但问答失败

- 检查 `/api/chat`
- 检查页面当前是否切到了 `Single Query` 模式
- 检查 JWT 或开发身份配置
- 检查 Redis / Qdrant / Elasticsearch 健康状态

### 证据文档打不开

- 检查 `/api/media/{doc_id}` 是否返回 401 / 403 / 404 / 503
- 检查文档元数据中的 `status`
- 检查 MinIO 是否可用

### 管理员面板报权限错误

- 检查登录账号是否真的是 `admin` 角色
- 检查 JWT 是否过期
- 检查生产环境是否混入旧的前端缓存

## 6. 备份建议

- SQLite 用户库：每日备份
- Redis：保留 AOF / RDB
- Qdrant：目录快照
- MinIO：对象存储快照
- `config.json` / `.env`：版本化与密钥分离管理
