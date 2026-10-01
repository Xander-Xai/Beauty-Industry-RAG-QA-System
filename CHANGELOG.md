# Changelog

## [2.5.0] - 2026-06-24

### Fixed — 安全加固 + 基础设施修复 (9 项)

**安全加固 (S-01~S-04):**
- api/routes.py: `/api/stats` 和 `/api/metrics` 添加 `Depends(require_identity)` 认证，防止未授权访问系统运行指标
- api/middleware.py: 移除 CORS 生产模式检查的 `try/except ImportError`，改为直接导入 `is_production_mode()`，确保生产环境未设置 `CORS_ORIGINS` 时硬性阻止启动
- api/routes_auth.py: 登录速率限制增加 `X-Forwarded-For` 解析，支持反向代理场景下获取客户端真实 IP
- api/routes_auth.py: 登录速率限制增加 Redis 后端（`ratelimit:login:{ip}`），Redis 不可用时自动降级至内存；多 worker/多容器部署下共享限流状态

**基础设施修复 (S-05~S-08):**
- app.py: 从已弃用的 `@app.on_event("startup")` / `@app.on_event("shutdown")` 迁移至 `lifespan` 上下文管理器模式，消除 17 个 FastAPI 弃用警告
- docker-compose.yml: 启用 Elasticsearch `xpack.security.enabled=true`，默认使用 `elastic` 用户 + 环境变量密码；健康检查使用认证请求
- docker-compose.yml: app 服务增加 `REDIS_PASSWORD`、`ELASTICSEARCH_USERNAME`、`ELASTICSEARCH_PASSWORD` 环境变量传递
- retrieval/bm25_retriever.py: ES 客户端增加环境变量 `ELASTICSEARCH_USERNAME`、`ELASTICSEARCH_PASSWORD` 读取优先（兜底 `config.json`）

**跨 worker 会话共享 (S-09):**
- core/pipeline_context.py: `SessionState` 增加 Redis 持久化支持；`get_or_create` 优先从 Redis 加载会话（key `session:{id}`，TTL 7200s）；`add_round`/`lock_evidence` 自动持久化变更

### Changed
- docker-compose.yml: ES 启用 xpack security
- .env.example: ES 用户名/密码默认值改为 `elastic` / `REPLACE_WITH_STRONG_PASSWORD`
- tests: 更新 stats/metrics 测试用例适配新的认证要求
- 测试结果: **608 passed, 0 failed, 0 on_event warnings**

---

## [2.4.0] - 2026-06-24

### Fixed — 前后端联调一致性 + 生产部署修复 (6 项)

**前后端契约修复 (F-01~F-04):**
- frontend: `DEFAULT_METADATA.rbac` 补充 `roles: {}` 和 `departments: {}` 字段，防止 `/api/auth/metadata` 加载失败时前端崩溃
- frontend: `authRequired` 改为直接信任后端 `metadata.auth.auth_required`，不再自行使用 `??` 计算兜底逻辑（消除与后端逻辑漂移的风险）
- frontend: 引入统一 `parseResponseError()` 错误解析函数，同时兼容 `ErrorResponse.error`、`ErrorResponse.detail` 及嵌套 `detail.detail` 三种错误响应格式
- frontend: `sendMessage()`、`openEvidence()`、`handleLogin()`、`fetchSessionHistory()` 等所有错误处理路径统一使用 `parseResponseError()`

**生产部署修复 (F-05~F-06):**
- app.py: `uvicorn.run(reload=True)` 改为仅在非生产模式下启用热重载（`reload=_get_sys_config().deployment_mode != "production"`）
- config.json: `auth.dev_mode` 默认值从 `true` 调整为 `false`，避免部署时意外暴露开发模式

### Changed
- config.json: dev_mode 默认关闭
- README.md: 更新测试计数 (359→608)、补充前后端契约说明、记录生产修复
- 测试结果: **608 passed, 0 failed**

---

## [2.3.0] - 2026-06-06

### Fixed — 矛盾统一 + Bug 修复 + GAP 补全 (16 项)

**参数统一 (T-01~T-05):**
- safety_factor: config.json 从 0.75 统一为 PRD 值 0.7
- KV 四级降级阈值: 代码从 0.80/0.88/0.93/0.96 统一为 PRD 值 0.70/0.80/0.90/0.95
- config key 重命名: kv_utilization_threshold→kv_pressure_truncate, kv_pressure_critical→kv_pressure_soft_stop, 新增 kv_pressure_tighten/kv_pressure_critical
- pressure 分母 Bug: _pressure_unlocked() 从除以 kv_total(8GB) 修正为除以 kv_budget(5.6GB)
- OUTPUT_MAP 统一: 7 类业务类型 (regulation=1024, development=768, formulation=768, ingredient=512, product=512, general=512, short=256) 在 OUTPUT_MAP/config.json/gateway 三处统一

**Bug 修复 (T-06~T-09):**
- api-gateway: _generate_without_context() 接受 dynamic target_model/max_output_tokens/business_type 参数
- PRD §3.1: 数据范围更新为 配方3000+/产品1500+/成分2000+
- api/routes_auth.py: 补充 logging 导入，修复 NameError
- tests: 修复 test_complexity_evaluator CWD 竞争问题

**功能补全 (T-10~T-14):**
- admission/kv_admission.py: 新增 get_metrics() 导出有效并发数/吞吐模型等 Prometheus 指标
- monitoring-service/metrics_collector.py: 补全 NLI 矛盾率/BLIP 触发率/CLIP 超时率/Redis 降级状态等 7 项缺失指标
- common/audit.py: 审计日志持久化至 Redis Stream (XADD, maxlen=10000)
- offline/document_processor.py: 增量更新差异检测 (mtime+md5)，状态文件 + --force-full 支持

### Changed
- config.json: admission_control 部分重构（key 重命名 + 四级阈值）
- config.json: safety_factor 0.75→0.7
- PRD.md §3.1: 数据范围补充产品维度

**测试结果:** 全量回归测试结果:
- 总测试数: 359
- 通过数: 359
- 失败数: 0
- 跳过数: 0

全部 359 个测试通过，无失败，无跳过。7 个 warnings 均为 FastAPI `on_event` 弃用提示，不影响功能。

注意：第一次运行出现的 8 个 `test_kv_admission.py` 失败属于非确定性问题（测试并行执行时的状态竞争），单独运行该模块及第二次全量运行均已全部通过。

---

## [2.2.0] - 2026-06-06

### Fixed — 全量代码审查 16 项问题修复

- **api-gateway/routers/generation.py: CRITICAL — 准入控制变量使用在赋值之前**
  - `max_output_tokens` 和 `business_type` 在准入检查步骤引用但未赋值
  - 将准入控制检查移动到查询改写和复杂度评估之后
  - 同时移除了重复的复杂度评估和输出长度计算代码块

- **core/pipeline.py + api/routes.py: _fallback_rewrite 传入 None 作为 self**
  - `_fallback_rewrite` 从实例方法改为 `@staticmethod`
  - 修正 `ComplexityEvaluator` 实例化方式（独立创建而非依赖 self）
  - 更新 `routes.py` 调用签名

- **retrieval/evidence_gate.py: NLI 批量推理参数签名不匹配**
  - `_batch_nli_inference` 期望 `(premise, hypothesis)` 元组列表
  - 实际传入两个独立列表，导致永远 fallback 到逐条推理
  - 修正为元组列表格式

- **api/routes.py + frontend: 前端图片上传 FormData 与后端 JSON 不兼容**
  - 新增 `POST /api/query/upload` 端点，支持 `multipart/form-data`
  - 前端图片上传请求改为调用 `/api/query/upload`

- **retrieval/bm25_retriever.py: JSON 加载使用破坏性字符串替换**
  - 移除 `json.loads(f.read().replace('\\"', '"'))` 中的 `.replace()`
  - 改用标准 `json.load(f)`

- **api-gateway/main.py: 系统名称为"汽车知识"（项目遗留错误）**
  - 全部替换为"化妆品企业级多模态 RAG 智能问答系统"

- **auth/user_store.py: RBAC 角色掩码与 config.json 不一致**
  - 从硬编码值改为从 `config.json` 动态读取
  - 修复 `update_user_roles` 使用 `sum()` 而非位运算 `|=`

- **retrieval/rerank_batch_aggregator.py: 队列延迟计算错误**
  - `_execute_batch_sync` 增加 `submit_time` 参数
  - `_process_batch` 传入批次最早提交时间

- **docker-compose.yml: Redis 密码环境变量缺失**
  - app 服务增加 `REDIS_CACHE_PASSWORD` 环境变量

- **config.json: 缺少 l1_max_entries + jwt_secret 为空**
  - 添加 `l1_max_entries: 1000`
  - 设置开发用 `jwt_secret`

- **app.py: SessionState 会话无定期清理**
  - 新增启动时 asyncio 后台任务，每 5 分钟清理过期会话

- **monitoring/otel_tracer.py: 线程锁初始化竞态条件**
  - `_thread_lock` 在 `__init__` 中直接初始化

- **api-gateway/routers/generation.py: CJK 字符范围不完整**
  - 扩展覆盖 CJK 扩展 A 区（U+3400-U+4DBF）

### Changed

- **tests/test_user_store.py: 更新测试用例**
  - 角色和部门名称改为与 config.json 一致
  - 角色掩码断言值更新

### 修复文件清单

| 文件 | 修复类型 |
|------|---------|
| `api-gateway/routers/generation.py` | CRITICAL: 变量顺序 + CJK 范围 |
| `core/pipeline.py` | HIGH: @staticmethod |
| `api/routes.py` | HIGH: fallback 签名 + multipart 端点 |
| `retrieval/evidence_gate.py` | HIGH: NLI 参数签名 |
| `frontend/src/App.jsx` | HIGH: 上传端点 |
| `retrieval/bm25_retriever.py` | MEDIUM: JSON 加载 |
| `api-gateway/main.py` | MEDIUM: 项目名称 |
| `auth/user_store.py` | MEDIUM: RBAC 角色对齐 |
| `retrieval/rerank_batch_aggregator.py` | MEDIUM: 队列延迟 |
| `docker-compose.yml` | MEDIUM: Redis 密码 |
| `config.json` | MEDIUM: 配置补全 |
| `app.py` | MEDIUM: 会话清理 |
| `monitoring/otel_tracer.py` | LOW: 线程锁 |
| `tests/test_user_store.py` | 测试更新 |

## [2.1.0] - 2026-06-05

### Fixed

- **core/pipeline.py: 修复 `is_complex` 变量使用顺序 (GAP-15, P0)**
  - 复杂度评估移至模型路由之前，消除 `NameError` 崩溃
  - 新增 evaluator 异常兜底（默认 simple + degraded）
  - 新增 `tests/test_pipeline_ordering.py`

- **tests/test_nli_service.py: 修复测试污染问题**
  - torch mock 由 `setdefault` 改为直接赋值，确保全量测试套件下 fake torch 生效
  - 新增 autouse fixture 管理 mock 生命周期

### Added

- **api/routes.py: 新增 `GET /api/media/{doc_id}` (GAP-16, P1)**
  - Qdrant 文档权限元数据查询 + RBAC 二次校验
  - MinIO 签名 URL 生成（60s 有效期）
  - 403/404/503 错误处理
  - 新增 `tests/test_media_route.py`（13 个测试）

- **api/routes.py: 新增 `GET /metrics` (GAP-18, P1)**
  - Prometheus text 格式指标暴露（`text/plain; charset=utf-8`）
  - 新增 `tests/test_metrics_endpoint.py`（16 个测试）

- **run_services.py: NLI 端点从硬编码改为真实推理 (GAP-17, P1)**
  - 启动时加载 DeBERTa-v3-mnli 模型
  - 模型不可用时返回 HTTP 501 + 合约说明
  - 新增 `tests/test_nli_service.py`（9 个测试）

### Changed

- **offline/scheduler.py: Qdrant 归档逻辑优化 (GAP-19, P2)**
  - 主路径使用 `upsert(status='archived')` 替代 delete
  - 降级兜底：upsert 失败时 delete + reinsert
  - 新增 `tests/test_archive_expired.py`（7 个测试）

- **docs/GAP_IMPL_PLAN.md: 更新实施计划**
  - 标记 GAP-15 ~ GAP-19 全部完成
  - 补充执行验证结果

### Test Coverage

- 新增 5 个测试文件，共 47 个测试用例
- 全量回归：**325 passed, 0 failed** (19.89s)
