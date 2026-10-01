# 化妆品行业 RAG 问答系统 — 上线前检查清单

> 这份清单基于当前仓库真实状态整理，不再把尚未闭环的能力写成“默认已可上线”。

## P0 阻塞项

- [ ] 确认已准备外部 Qdrant/Elasticsearch 索引；本仓库不含文档 ingestion，实现状态跟踪于 [Issue #2](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/2)
- [ ] 采用 `app.py` 单体 `/api/*` 主线；微服务目录需要独立验证其与当前前端的契约
- [ ] 生成并配置 JWT 密钥
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
- [ ] 验证管理员角色不会被 `rd` 等普通角色误判
- [ ] 验证前端构建产物可由 FastAPI 正确挂载
- [ ] 验证 `Dockerfile` 健康检查命中 `/api/health`

## P1 数据与内容验证

- [ ] 核对 Qdrant 集合名：`rag_text_768`、`rag_image_512`
- [ ] 核对 Elasticsearch 索引：`cosmetics_docs`
- [ ] 抽样检查文档元数据包含 `doc_id / role_mask / dept_mask / status`
- [ ] 抽样验证至少 10 个真实业务问题

## P2 运维准备

- [ ] 导入 Grafana 仪表盘
- [ ] 配置 Prometheus 抓取
- [ ] 制定 Redis / Qdrant / MinIO 备份策略
- [ ] 准备 HTTPS 与反向代理配置
- [ ] 组织管理员与运维演练

## 离线管线状态

`run_offline.py ingest-text` 支持受限的 UTF-8 TXT → configured BGE adapter → Qdrant 文本导入切片；`seal-epoch` 封存已准备好的快照。CI 使用确定性测试 embedder，未验证真实 BGE 模型下载或推理，也不代表完整生产离线管线已验证。PDF/DOCX/XLSX、OCR、CLIP、Elasticsearch 写入、调度与完整重建仍未实现。`rewrite-feedback` 是独立 utility，不属于该 TXT 导入路径。
