# Changelog

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
  - Milvus 文档权限元数据查询 + RBAC 二次校验
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

- **offline/scheduler.py: Milvus 归档逻辑优化 (GAP-19, P2)**
  - 主路径使用 `upsert(status='archived')` 替代 delete
  - 降级兜底：upsert 失败时 delete + reinsert
  - 新增 `tests/test_archive_expired.py`（7 个测试）

- **docs/GAP_IMPL_PLAN.md: 更新实施计划**
  - 标记 GAP-15 ~ GAP-19 全部完成
  - 补充执行验证结果

### Test Coverage

- 新增 5 个测试文件，共 47 个测试用例
- 全量回归：**325 passed, 0 failed** (19.89s)
