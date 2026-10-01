# 化妆品行业 RAG 问答系统 — 上线前检查清单

> 这份清单基于当前仓库真实状态整理，不再把尚未闭环的能力写成“默认已可上线”。

## P0 阻塞项

- [ ] 下载模型权重（PaddleOCR、CLIP、BGE 等）至 `models/` 目录；离线导入命令：`python3 run_offline.py --mode create-index` 创建 Qdrant Collection/ES 索引，`python3 run_offline.py --mode incremental` 导入文档
- [ ] 明确上线主线是 `app.py` 单体还是微服务网关；当前前端只按单体 `/api/*` 主线验证过
- [ ] 生成并配置 JWT RS256 密钥对（`keys/private.pem`、`keys/public.pem`）
  ```bash
  mkdir -p keys
  python3 -c "from auth.jwt_auth import generate_keypair; generate_keypair('./keys')"
  ```
- [ ] 设置 `AUTH_DEV_MODE=false`（`config.json` 中 `auth.dev_mode` 现在默认为 `false`，但建议检查确认）
- [ ] 设置 `CORS_ORIGINS`（生产模式未设置时后端硬性阻止启动）
- [ ] 设置 `ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD`（v2.5.0 ES xpack.security 启用）
- [ ] 设置 `REDIS_PASSWORD`
- [ ] 设置 `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`
- [ ] 设置 `SERVICE_AUTH_TOKEN`
- [ ] 确认项目根目录 `.env` 已被当前启动进程自动加载
- [ ] 构建前端，确认 `frontend/dist` 已生成
- [ ] 验证 `GET /api/health`（无认证）、`GET /api/stats`（需JWT）、`GET /api/metrics`（需JWT）、`GET /api/auth/metadata`（无认证）
- [ ] 验证管理员可登录、创建用户、更新角色/部门
- [ ] 验证不同角色访问 `/api/media/{doc_id}` 时权限正确

## P1 联调与功能验证

- [ ] 验证前端登录后可自动刷新 Token
- [ ] 验证前端 `Single Query` 对应 `POST /api/query`
- [ ] 验证多轮对话使用 `POST /api/chat`
- [ ] 验证前端 `Session` 面板对应 `GET /api/dialog_history`
- [ ] 验证前端 `Stats` 面板对应 `GET /api/stats`（需要 JWT 认证）
- [ ] 验证证据文档通过预签名链接打开，而不是直接裸跳转 API
- [ ] 验证管理员角色不会被 `rd` 等普通角色误判
- [ ] 验证前端构建产物可由 FastAPI 正确挂载
- [ ] 验证 `Dockerfile` 健康检查命中 `/api/health`
- [ ] 验证 ES xpack.security 连接正常（`GET /api/health` 中 ES 依赖为 `true`）
- [ ] 验证多 worker 部署下 Session 可跨进程共享（相同 session_id 可被不同 worker 处理后继续对话）
- [ ] 运行 RAGAS 黄金数据集评估并保存基线（`python -m tests.evaluation.ragas_eval --tag production-baseline`）

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

## 当前可用的离线命令

以下离线命令已通过 `offline/` 模块实现，可直接执行：

```bash
# 创建 Qdrant Collection 与 ES 索引
python3 run_offline.py --mode create-index

# 增量更新（基于文件指纹检测新增/修改文档）
python3 run_offline.py --mode incremental

# 全量重建
python3 run_offline.py --mode full

# 反馈闭环（参数优化）
python3 run_offline.py --mode feedback
python3 run_offline.py --mode rewrite-feedback

# 生成 Mock 数据
python3 -m data.mock_generator data/mock_data
```
