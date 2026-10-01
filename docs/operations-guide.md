# 化妆品行业 RAG 问答系统运维手册

## 1. 先明确当前运维范围

当前仓库已经能确认的在线运维对象是单体 FastAPI 应用：

- 入口：`app.py`
- 健康检查：`GET /api/health`
- 统计：`GET /api/stats`
- 指标：`GET /api/metrics`

微服务目录仍在仓库中，但不应默认按“已完成整套线上运维验证”处理。

## 2. 当前值得盯的指标

### 应用可用性

- `/api/health`
- `/api/stats`
- `/api/metrics`
- `/api/auth/metadata`

### 认证与权限

- 登录成功率
- Token 刷新成功率
- `/api/auth/users` 管理接口返回码
- `/api/media/{doc_id}` 的 401 / 403 / 404 比例

### 依赖健康

- Redis 可连通性
- Qdrant 可连通性
- Elasticsearch 可连通性
- MinIO 可用性

## 3. 当前已知运维风险

- **知识库数据准备**：当前只支持通过 `python3 run_offline.py ingest-text ...` 将 UTF-8 TXT 分块并写入 Qdrant 文本 collection。PDF/DOCX/XLSX、OCR、CLIP、ES 写入、调度与完整重建仍未实现。BGE 模型须按 `config.json` 配置并由操作者准备；CI 不下载模型。后续实现见 [Issue #2](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/2)。
- **单体与微服务并存**：排障时必须先确认当前请求到底走的是 `app.py` 还是单独网关/微服务。
- **开发身份开关**：如果生产环境误保留 `AUTH_DEV_MODE=true`，会形成身份伪造风险。
- **运行配置来源**：服务现在会自动读取项目根目录 `.env`；排障时要同时检查 `.env` 与进程环境。

## 4. 生产巡检建议

### 每次部署后

1. 访问 `GET /api/health`
2. 访问 `GET /api/auth/metadata`
3. 使用管理员账号登录
4. 验证管理员创建用户、更新角色/部门
5. 用不同角色验证证据文档访问权限
6. 打开首页，确认构建后的前端已被静态挂载
7. 打开 `Session` / `Stats` 面板，确认它们分别命中 `/api/dialog_history` 和 `/api/stats`

### 每日

1. 检查 Redis / Qdrant / Elasticsearch / MinIO 容器状态
2. 抽样检查 `/api/stats`
3. 检查错误日志中的 401 / 403 / 500

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
