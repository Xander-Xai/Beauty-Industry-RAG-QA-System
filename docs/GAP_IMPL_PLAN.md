# 未完成功能实施计划

> 生成日期：2026-06-05
> 最后更新：2026-06-05（Phase 1-3 全部完成）
> 基于 PRD.md 与代码审查对比，列出所有未实现或部分实现的功能项。

---

## 实施详情

### GAP-01: 跨请求 GPU 微批聚合（时间窗口模式）✅
- **PRD 章节**: §7.3
- **修改文件**: `retrieval/rerank_batch_aggregator.py`
- **实现内容**:
  - 新增 `_PendingItem` 数据类和 `_batch_worker_loop` 后台线程
  - 实现 `_flush_if_ready` 方法：在 time_window_ms 窗口到期或累积 pair 数 >= max_batch_size 时触发推理
  - 新增 `submit_batch()` 异步提交接口，返回 `Future[list[float]]`
  - `batch_predict()` 保留为同步兼容入口
  - `get_stats()` 新增 `pending_count` 实时队列深度指标

### GAP-02: A/B 实验平台接入在线管线 ✅
- **PRD 章节**: §12.2
- **修改文件**: `core/pipeline.py`, `core/pipeline_context.py`, `retrieval/evidence_gate.py`
- **实现内容**:
  - Pipeline 在身份解析后调用 `ab_platform.get_running_experiments()` 获取运行中实验
  - 通过 `assign_variant(user_id, experiment_id)` 确定性分流
  - `get_variant_config()` 获取实验覆盖配置（evidence_gate_weights/thresholds）
  - `evidence_gate.evaluate()` 新增 `weights_override` / `thresholds_override` 参数
  - 请求完成后 `record_metric()` 记录 evidence_score 和 latency_ms
  - `RequestContext` 新增 `ab_experiment` / `ab_variant` 字段

### GAP-03: Prompt 反馈闭环接通 ✅
- **PRD 章节**: §4.4, §12.2
- **修改文件**: `rewrite/query_rewriter.py`
- **实现内容**:
  - `QueryRewriter` 新增 `_get_prompt_template()` 方法
  - 启动时懒加载 `RewriteFeedback.get_active_prompt_version()` 获取活跃 Prompt 版本
  - 不存在活跃版本则回退到默认硬编码模板
  - 新增 `reload_prompt_template()` 方法供反馈循环热更新后调用
  - `rewrite()` 中改用 `self._get_prompt_template().format(...)` 替代硬编码常量

### GAP-04: CLIP 120ms 超时丢弃机制 ✅
- **PRD 章节**: §4.5
- **修改文件**: `retrieval/parallel_recall.py`
- **实现内容**:
  - `_recall_clip()` 重写为使用 `ThreadPoolExecutor` + `future.result(timeout=clip_timeout_s)`
  - 超时时间从 `config.clip_sync.timeout_ms` 读取（默认 120ms）
  - `FuturesTimeout` / `TimeoutError` 时返回空列表并记录 WARNING 日志
  - 主链路不被 CLIP 阻塞，保证鲁棒性

### GAP-05: Platt Scaling 校准 ✅
- **PRD 章节**: §7.3
- **修改文件**: `retrieval/cross_encoder_ensemble.py`
- **实现内容**:
  - 新增 `PlattScaler` 类：sigmoid(A * score + B) 校准函数
  - 支持 per-model 校准参数（ce_a, ce_b, ensemble）
  - 冷启动默认 A=-1.0, B=0.0（近似 S 形映射）
  - `update_params()` 方法供离线反馈闭环更新校准参数
  - `CrossEncoderEnsemble.rerank()` 改为 `calibrated_scores = platt_scaler.calibrate_batch(raw_ensembles)`
  - 校准参数从 `config.retrieval.cross_encoder.platt_scaling` 读取

### GAP-06: Jaeger 导出器启用 ✅
- **PRD 章节**: §12
- **修改文件**: `monitoring/otel_tracer.py`
- **实现内容**:
  - `_try_init_otel()` 从 `config.monitoring.jaeger` 读取配置
  - `enabled=true` 时导入 `JaegerExporter` 并添加 `BatchSpanProcessor`
  - 支持配置 `agent_host` 和 `agent_port`
  - 导入失败时优雅降级（不影响 OTel 本地模式）

### GAP-07: Prometheus 指标导出 ✅
- **PRD 章节**: §12
- **修改文件**: `monitoring-service/main.py`, `monitoring/otel_tracer.py`
- **实现内容**:
  - `MetricsCollector` 新增 `to_prometheus_text()` 方法
  - monitoring-service 新增 `GET /metrics` 端点，输出标准 Prometheus text 格式
  - 支持 counter / gauge / histogram(summary) 三种类型
  - 无需认证（Prometheus 标准行为）
  - media_type 设为 `text/plain; charset=utf-8`

### GAP-08: 告警通知渠道 ✅
- **PRD 章节**: §12
- **修改文件**: `monitoring-service/alerting.py`
- **实现内容**:
  - `AlertingManager.__init__` 加载 `config.alerting.notification_channels`
  - `_send_notifications()` 遍历所有渠道，按 `min_severity` 过滤
  - 支持 `webhook` 类型：HTTP POST JSON 到指定 URL
  - 支持 `slack` 类型：Slack Incoming Webhook 格式化消息（含 emoji）
  - `_notified_alerts` 集合避免重复通知（仅首次触发时通知）
  - `clear_alert()` 同时清除通知记录

### GAP-09: 审计日志 intercept_reason 填充 �
- **PRD 章节**: §11
- **修改文件**: `core/pipeline.py`
- **实现内容**:
  - `_handle_rejection()` 新增 `log_audit_event(event_type="request_rejected", reject_reason=f"admission_{reason}")`
  - Evidence Gate 拒答时记录 `reject_reason=f"evidence_gate_{decision}_score=..."`
  - Answer Gate 拒答时记录 `reject_reason=f"answer_gate_nli_contradiction=..."`
  - 所有拦截事件统一 `event_type="request_rejected"`，通过 `reject_reason` 区分原因

### GAP-10: MinIO 端点权限二次校验 ✅
- **PRD 章节**: §6, §10
- **修改文件**: `api-gateway/routers/generation.py`
- **实现内容**:
  - `/api/media/{doc_id}` 端点新增 Milvus 文档权限查询
  - 通过 `embedding_service.milvus_client.query()` 获取文档的 `role_mask`, `dept_mask`, `status`
  - 调用 `is_allowed()` 比对用户权限与文档权限
  - 权限不匹配返回 403
  - Milvus 不可用时降级到 generation-service 代理校验

### GAP-12: L1 缓存 LRU 淘汰 ✅
- **PRD 章节**: §10
- **修改文件**: `cache/redis_cache.py`
- **实现内容**:
  - `self._l1` 从 `dict` 改为 `collections.OrderedDict`
  - `get()` 命中时调用 `self._l1.move_to_end(key)` 标记为最近使用
  - `set()` 淘汰时调用 `self._l1.popitem(last=False)` 淘汰最久未使用的
  - `set()` 新 key 时 `self._l1.move_to_end(key)` 标记为最近使用
  - 测试文件同步更新为 `OrderedDict()`

### GAP-13: JWT 算法统一 ✅
- **PRD 章节**: §11
- **修改文件**: `auth/user_identity.py`
- **实现内容**:
  - `parse_from_token()` 优先尝试 RS256 验证：`from auth.jwt_auth import verify_token`
  - RS256 失败时回退到 HS256（向后兼容）
  - 提取公共方法 `_extract_identity_from_payload()` 消除重复解析逻辑
  - 保证生产环境使用 RSA 非对称密钥验证

### GAP-14: 代码重复消除 ✅
- **修改文件**: `generation-service/kv_admission.py`, `cache-service/redis_cache.py`
- **实现内容**:
  - `generation-service/kv_admission.py` 改为 14 行包装器，`from admission.kv_admission import KVAdmissionControl`
  - `cache-service/redis_cache.py` 改为 14 行包装器，`from cache.redis_cache import RedisCache`
  - 消除两处 ~200 行的完全重复代码
  - 确保 `sys.path` 包含项目根目录

### GAP-11: BLIP 在线触发逻辑 ✅
- **PRD 章节**: §6
- **修改文件**: `models/blip_service.py`（新建）, `core/pipeline.py`, `core/pipeline_context.py`, `config.json`, `monitoring/otel_tracer.py`, `monitoring-service/alerting.py`
- **实现内容**:
  - **新建 `models/blip_service.py`**：
    - `BLIPTargetDetector`：Rule + BERT 双路触发决策
      - 条件 1：CLIP 图像结果命中 → 直接触发
      - 条件 2：关键词规则评分（VISUAL_KEYWORDS 词表）≥ 0.6
      - 条件 3：BERT 意图分类评分（formulation/ingredient + 视觉关键词）≥ 0.6
    - `BLIPInferenceService`：BLIP 图像描述生成
      - 懒加载 `BlipForConditionalGeneration` 模型
      - 支持条件/无条件 image captioning
      - 结果缓存 TTL 1h（线程安全 OrderedDict）
      - GPU 推理超时 120ms 告警
      - 缓存大小限制 500 条，LRU 淘汰
      - `get_trigger_rate()` / `get_stats()` 可观测性
  - **Pipeline 集成**：
    - 在 ⑧ 并行召回和 ⑨ Union 合并之间插入 ⑧-b BLIP 触发步骤
    - `_maybe_trigger_blip()` 方法：决策 → 批量推理 → 追加描述到 RecallResult.content
    - 最多处理 5 张图像（`config.blip.max_images_per_request`）
    - BLIP 描述格式：`[BLIP图像描述] {caption}` 追加到原文 content
    - 触发失败不影响主链路（try/except 降级）
  - **监控集成**：
    - `RequestContext` 新增 `blip_triggered` 字段
    - `MetricsCollector.record_request()` 记录 `blip.triggered` / `blip.total` 计数
    - `AlertingManager` 新增 `blip_trigger_rate_high` 告警规则（>10% 持续 300s）
  - **配置**：
    - `config.json` BLIP 路径修正为 `./blip-image-captioning-large`
    - 新增 `max_images_per_request: 5`, `cache_ttl_seconds: 3600`

---

## 未完成项

### GAP-15 (P0): core/pipeline.py 变量使用顺序修复 ✅
- **修改文件**: `core/pipeline.py`, `tests/test_pipeline_ordering.py`
- **实现内容**:
  - 将复杂度评估（`is_complex` 赋值）移至模型路由之前
  - 新增 evaluator 异常兜底（默认 simple + degraded）
  - 新增 2 个单元测试覆盖正常路径与降级路径

### GAP-16 (P0): 单体缺少 /api/media/{doc_id} 路由 ✅
- **修改文件**: `api/routes.py`, `tests/test_media_route.py`
- **实现内容**:
  - 新增 `GET /api/media/{doc_id}`
  - 权限二次校验（Milvus metadata + `is_allowed()`）
  - MinIO 签名 URL（60s）+ 403/404/503 错误处理
  - 新增 13 个单元测试

### GAP-17 (P1): NLI 端点硬编码分数修复 ✅
- **修改文件**: `run_services.py`, `tests/test_nli_service.py`
- **实现内容**:
  - 替换硬编码 NLI 分数为真实推理/501 合约
  - 新增 9 个单元测试

### GAP-18 (P1): 单体缺少 /metrics 端点 ✅
- **修改文件**: `api/routes.py`, `tests/test_metrics_endpoint.py`
- **实现内容**:
  - 新增 `GET /metrics` 返回 Prometheus text
  - 新增 16 个单元测试

### GAP-19 (P2): Milvus 归档改用 upsert(status=archived) ✅
- **修改文件**: `offline/scheduler.py`, `tests/test_archive_expired.py`
- **实现内容**:
  - 主路径使用 upsert 标记 archived
  - 降级兜底 delete + reinsert
  - 新增 7 个单元测试

### 执行验证
- `pytest tests/test_pipeline_ordering.py tests/test_media_route.py tests/test_metrics_endpoint.py tests/test_nli_service.py tests/test_archive_expired.py -v` 结果：**47 passed**

（全部 19 项 GAP 已完成）
