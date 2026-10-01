# 化妆品行业 RAG 问答系统 — 上线前检查清单

> 这份清单基于当前仓库真实状态整理，不再把尚未闭环的能力写成“默认已可上线”。

## P0 阻塞项

- [ ] 准备知识库索引：运行 `run_offline.py create-index`，或确认已存在由其他流程建立的 Qdrant/Elasticsearch 索引
- [ ] 采用 `app.py` 单体 `/api/*` 主线；微服务目录需要独立验证其与当前前端的契约
- [ ] 生成并配置 JWT 密钥
- [ ] 确认生产认证为 RS256（`JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` / `JWT_ALGORITHM=RS256`）；仅当需要兼容旧 HS256 token 时才设置 `JWT_SECRET`
- [ ] 验证 RS256 登录 → refresh → 受保护接口鉴权链路
- [ ] 设置 `AUTH_DEV_MODE=false`
- [ ] 设置 `CORS_ORIGINS`
- [ ] 设置 `REDIS_PASSWORD`
- [ ] 设置 `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`
- [ ] 设置 `SERVICE_AUTH_TOKEN`
- [ ] 确认项目根目录 `.env` 已被当前启动进程自动加载
- [ ] 构建前端，确认 `frontend/dist` 已生成
- [ ] 验证 `GET /api/health`、`GET /api/stats`、`GET /api/metrics`、`GET /api/auth/metadata`
- [ ] 验证管理员可登录、创建用户、更新角色/部门
- [ ] 验证不同角色访问 `/api/media/{doc_id}` 时权限正确

## P1 联调与功能验证

- [ ] 验证前端登录后可自动刷新 Token
- [ ] 验证前端 `Single Query` 对应 `POST /api/query`
- [ ] 验证多轮对话使用 `POST /api/chat`
- [ ] 验证前端 `Session` 面板对应 `GET /api/dialog_history`
- [ ] 验证前端 `Stats` 面板对应 `GET /api/stats`
- [ ] 验证证据文档通过预签名链接打开，而不是直接裸跳转 API
- [ ] 验证文档或查询权限元数据缺失/非法时 fail closed（拒绝访问，而不是按公开处理）
- [ ] 验证管理员角色不会被 `rd` 等普通角色误判
- [ ] 验证前端构建产物可由 FastAPI 正确挂载
- [ ] 验证 `Dockerfile` 健康检查命中 `/api/health`

## P1 数据与内容验证

- [ ] 核对 Qdrant 集合名：`rag_text_768`、`rag_image_512`
- [ ] 核对 Elasticsearch 索引：`cosmetics_docs`
- [ ] 抽样检查文档元数据包含 `doc_id / role_mask / dept_mask / status / doc_version_epoch`
- [ ] 准备生产 BGE 模型资产
- [ ] 启用视觉检索时准备生产 CLIP 模型资产
- [ ] 需要 OCR 时安装 PaddleOCR/PaddlePaddle 运行时
- [ ] 运行完整快照构建（`full-rebuild`）并通过快照校验
- [ ] 验证图像检索遵循 active epoch（legacy 点不泄漏到非 default epoch）
- [ ] 验证不同角色的 RBAC 边界
- [ ] 封存目标 epoch（`seal-epoch`）
- [ ] 显式切换 `config.json` 的 `knowledge_version_epoch` 并重启在线服务
- [ ] 保留上一 sealed epoch 以支持回滚
- [ ] 抽样验证至少 10 个真实业务问题
- [ ] 记录生产评测状态（真实模型 smoke / 质量评测是否为已验证结果）

## P2 运维准备

- [ ] 导入 Grafana 仪表盘
- [ ] 配置 Prometheus 抓取
- [ ] 制定 Redis / Qdrant / MinIO 备份策略
- [ ] 准备 HTTPS 与反向代理配置
- [ ] 组织管理员与运维演练

## 离线管线状态

`run_offline.py` 提供 `create-index`、`ingest`、`incremental-build`、`full-rebuild`、`seal-epoch`
（以及向后兼容的 `ingest-text`）。管线覆盖 TXT/PDF/DOCX/XLSX/图片、扫描页 OCR 路由、BGE/CLIP
adapter、Qdrant 文本/图像、Elasticsearch、增量 carry-forward、全量重建、快照校验与 epoch 封存，
并在确定性测试中验证。

真实 BGE/CLIP/PaddleOCR 需要外部模型/运行时资产，CI 不下载模型；真实模型 smoke 未执行时为
`EXTERNAL_MODEL_ASSET_REQUIRED`。`seal-epoch` 默认先校验（Qdrant text/image + Elasticsearch）
再封存；`--skip-validation` 仅为危险逃生口。封存后需手动切换 `knowledge_version_epoch` 才会
对在线检索生效。调度抽象与 Airflow DAG 已实现，但默认 Compose 不运行 Airflow，也未做真实
Airflow 执行验证。
