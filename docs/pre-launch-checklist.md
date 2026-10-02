# 化妆品行业 RAG 问答系统 — 上线前检查清单

> 这份清单基于当前仓库真实状态整理，不再把尚未闭环的能力写成“默认已可上线”。

> 部署形态基线：当前 canonical 部署是 **Docker Compose + FastAPI 单体 `app.py`**。本清单不以 Kubernetes、Kafka、GraphRAG 或 Multi-Agent 作为上线前置条件；`api-gateway/`、`retrieval-service/`、`generation-service/`、`monitoring-service/` 是可选组件，纳入生产前需要各自独立的部署与契约验证。

## P0 阻塞项

- [ ] 准备知识库索引：运行 `run_offline.py create-index`，或确认已存在由其他流程建立的 Qdrant/Elasticsearch 索引
- [ ] 采用 `app.py` 单体 `/api/*` 主线；微服务目录需要独立验证其与当前前端的契约
- [ ] 生成并配置 JWT 密钥
- [ ] 确认生产认证为 RS256（`JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` / `JWT_ALGORITHM=RS256`）；仅当需要兼容旧 HS256 token 时才设置 `JWT_SECRET`
- [ ] 验证 RS256 登录 → refresh → 受保护接口鉴权链路
- [ ] 设置 `AUTH_DEV_MODE=false`
- [ ] 设置 `CORS_ORIGINS`
- [ ] 设置 `REDIS_PASSWORD`
- [ ] 设置 `ELASTICSEARCH_USERNAME` / `ELASTICSEARCH_PASSWORD`，确认 ES 启用 `xpack.security`
- [ ] 设置 `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`
- [ ] 设置 `SERVICE_AUTH_TOKEN`
- [ ] （反向代理部署）设置 `TRUSTED_PROXIES`，确认代理确实追加 `X-Forwarded-For`
- [ ] 确认项目根目录 `.env` 已被当前启动进程自动加载
- [ ] 构建前端，确认 `frontend/dist` 已生成
- [ ] 验证 `GET /api/health` 公开可访问
- [ ] 验证 `GET /api/stats`、`GET /api/metrics` 未认证返回 401、带 token 返回 200
- [ ] 验证 `GET /api/auth/metadata`
- [ ] 验证管理员可登录、创建用户、更新角色/部门
- [ ] 验证不同角色访问 `/api/media/{doc_id}` 时权限正确
- [ ] 验证多 worker 下 Redis 会话/登录限流共享；Redis 停止后降级内存不崩溃

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
- [ ] 配置 Prometheus 抓取（`/api/metrics` 需要 Bearer token）
- [ ] 制定 Redis / Qdrant / MinIO 备份策略
- [ ] **执行一次备份恢复演练并记录结果**（策略文件存在不等于恢复可用）
- [ ] 准备 HTTPS 与反向代理配置
- [ ] 验证 HTTPS 终止与反向代理链路：证书、`X-Forwarded-For` 追加、`TRUSTED_PROXIES` 与实际客户端 IP 一致
- [ ] 组织管理员与运维演练

## P2 可观测性与证据产物

本仓库的确定性测试证明的是实现契约，不是生产运行证据。下面把「已实现」与「已验证」分开列出：前者由本仓库代码与测试覆盖，后者需要一份可复现的外部产物。**不要因为前者完成就勾选后者。**

已实现（`REPO_VERIFIED`，本仓库代码 + 确定性测试）：

- [x] performance artifact 契约（七文件、`EXECUTED`/`PARTIAL`/`BLOCKED`、未测量即 `null`）
- [x] 结构化企业动作审计（统一 schema、强制脱敏、request_id 关联、Redis Stream + JSONL 持久化）
- [x] SLO 与故障 Runbook（5 个目标 + 8 个处置流程；目标为 `DESIGN_TARGET`）
- [x] Prometheus 告警规则（6 条，全部基于真实 emit 的指标；阈值为 `DESIGN_TARGET`）
- [x] Grafana 最小仪表盘（10 个面板，仅真实指标）
- [x] OTLP exporter 实现（默认关闭、失败不影响业务、span 属性白名单）

仍需外部产物才算完成（`PENDING`）：

- [ ] 启用 OTLP 导出并在后端实际查询到本服务产生的 span。当前默认无 exporter，**exporter 已实现不代表闭环已验证**
- [ ] 在运行中的 Prometheus 里导入并评估 `monitoring/prometheus/alerts.yml`，确认规则能被加载且指标可抓取（需先生成 scrape bearer token 文件）
- [ ] 导入 Grafana 仪表盘，确认面板能被真实数据填充
- [ ] 产出 retrieval benchmark artifact（`artifacts/benchmarks/<run-id>/`），并核对 `docs/repository-truth-audit.md` 中的 artifact 验收字段。**当前没有 artifact，benchmark 框架 = `REPO_VERIFIED`，benchmark 结果 = `PENDING`**
- [ ] 产出负载/性能 artifact（吞吐、P95/P99、并发），或明确记录 PRD 中的延迟/QPS 数字仍为设计目标
- [ ] 记录真实 RAGAS evaluator 运行结果，或明确记录其依赖/凭据仍阻塞
- [ ] 完成真实 4B/14B vLLM GPU 拓扑验证（需要模型权重与 GPU）。**历史生产推理经验不构成该仓库的验证证据**

未覆盖的依赖故障场景（诚实缺口，非遗漏）：Qdrant 与 Elasticsearch 故障没有告警规则，因为本仓库未为它们输出任何 Prometheus 指标；目前靠 `GET /api/health` 巡检与 Runbook 流程覆盖。补齐它需要新增真实指标埋点，不应靠写一条指向不存在指标的规则来假装覆盖。

## 外部模型验证状态（尚未完成）

以下能力在代码与确定性测试中已实现，但真实资产/运行时验证仍未执行，**不得**因为本清单其他项完成而默认视为已验证：

- [ ] 真实配置的 BGE 模型 smoke（`EXTERNAL_MODEL_ASSET_REQUIRED`）
- [ ] 真实配置的 CLIP 模型 smoke（`EXTERNAL_MODEL_ASSET_REQUIRED`）
- [ ] 真实 PaddleOCR 运行时 smoke（默认不安装 OCR 运行时）
- [ ] 真实 Airflow DAG 执行（默认 Compose 不运行 Airflow）

确定性 fake embedder / fake OCR provider / mocked 客户端都不是真实模型或真实基础设施验证。

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
