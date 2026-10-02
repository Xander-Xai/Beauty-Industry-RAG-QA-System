# 化妆品行业 RAG 问答系统运维手册

## 0. 版本语义

本手册描述的运行配置以 `config.json` → `system.version` 为准（当前为 `2.3.0`）。仓库中部分文档与文件名使用的 “v2.5” 是 **历史 working milestone / development-phase 标签**，不是正式发布版本，也不改变本手册的运行时契约；详见 [版本策略](repository-truth-audit.md#version-policy)。

## 1. 先明确当前运维范围

当前仓库已经能确认的在线运维对象是单体 FastAPI 应用：

- 入口：`app.py`
- 健康检查：`GET /api/health`（公开）
- 统计：`GET /api/stats`（需要认证）
- 指标：`GET /api/metrics`（需要认证，Prometheus 抓取需 Bearer token）

微服务目录仍在仓库中，但不应默认按“已完成整套线上运维验证”处理。

会话状态与登录限流在配置 Redis 时跨 worker 共享；Redis 不可用时降级为进程内内存（此时多 worker 不共享，属于可接受的降级而非故障）。

## 2. 当前值得盯的指标

### 应用可用性

- `/api/health`（无需认证）
- `/api/stats`（配置监控账号的 Bearer token）
- `/api/metrics`（配置监控账号的 Bearer token）
- `/api/auth/metadata`（公开）

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

- **知识库数据准备**：通过 `run_offline.py` 构建离线快照（`full-rebuild` / `incremental-build`），
  `seal-epoch` 校验并封存，再手动切换 `knowledge_version_epoch` 激活。真实 BGE/CLIP 模型与可选
  PaddleOCR 运行时须由操作者准备；CI 使用确定性测试 embedder，不下载模型。操作细节见
  [数据管理手册](data-admin-guide.md)。
- **单体与微服务并存**：排障时必须先确认当前请求到底走的是 `app.py` 还是单独网关/微服务。
- **认证算法边界**：浏览器登录签发 RS256 access/refresh token，`common/auth` 以 RS256 验签为主；
  `JWT_SECRET`（HS256）仅为可选向后兼容回退。生产环境应配置 `JWT_PRIVATE_KEY_PATH` /
  `JWT_PUBLIC_KEY_PATH` / `JWT_ALGORITHM`，并仅在需要旧 HS256 兼容时设置 `JWT_SECRET`。
- **开发身份开关**：如果生产环境误保留 `AUTH_DEV_MODE=true`，会形成身份伪造风险。
- **运行配置来源**：服务现在会自动读取项目根目录 `.env`；排障时要同时检查 `.env` 与进程环境。
- **Elasticsearch 认证**：Compose 默认启用 `xpack.security.enabled=true`，需要 `ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD`。无认证或凭据错误的 ES 请求会失败，不要回退为关闭 security。
- **登录限流身份**：仅当 TCP 对端属于 `TRUSTED_PROXIES` 时才信任 `X-Forwarded-For`。反向代理未在允许列表内时，所有请求按代理 IP 计限流（可能误伤）；未配置代理却伪造 XFF 无法绕过限流。
- **监控端点认证**：`/api/stats`、`/api/metrics` 需要认证，裸 `curl` 会 401；监控系统需配置 token。

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
2. 抽样检查 `/api/stats`（带认证 token）
3. 检查错误日志中的 401 / 403 / 429 / 500
4. 检查 Redis 不可用时是否频繁出现降级日志（会话/限流内存回退）

### 离线知识库

1. 确认 `config.json` 的 `knowledge_version_epoch` 与预期 active epoch 一致
2. 检查 Qdrant 文本/图像 collection 与 Elasticsearch index mapping 是否存在且维度/字段正确
3. 运行或确认最近一次 `seal-epoch` 的快照校验通过（Qdrant text/image + Elasticsearch + RBAC）
4. 检查状态数据库 `knowledge_base.state_db_path` 的最近处理时间与失败源
5. 检查是否仍有未封存的 staging epoch；失败构建不应被自动封存
6. 确认已保留上一 sealed epoch 以便回滚
7. 审核反馈队列（`offline.feedback.store_path`）中 pending 记录

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

### 离线构建失败或 `seal-epoch` 被拒绝

- 模型资产缺失：确认 `embedding.text.model_path` / `embedding.image_clip.model_path` 存在
- OCR 报错：安装 `offline/requirements-ocr.txt`
- Qdrant `dimension does not match`：`create-index --recreate --yes`
- ES `field ... must be ...`：mapping 与 writer 不一致，重建 index
- `embedding version changed within epoch`：构建新 epoch 或 `full-rebuild`
- carry-forward `IncompatibleEmbeddingVersion`：改用 `full-rebuild`
- `knowledge epoch ... is sealed`：写入新的未封存 epoch
- 校验失败：查看 `seal-epoch` 报出的具体错误；不要用 `--skip-validation` 绕过
- `source is outside the configured data root`：移动 SOURCE 或提供 `--source-id`

## 6. 备份建议

- SQLite 用户库：每日备份
- Redis：保留 AOF / RDB
- Qdrant：目录快照
- MinIO：对象存储快照
- `config.json` / `.env`：版本化与密钥分离管理
