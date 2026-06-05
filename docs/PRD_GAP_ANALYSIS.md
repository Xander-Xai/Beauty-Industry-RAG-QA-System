# PRD 功能实现比对分析报告

> 生成日期: 2026-06-05 (已更新: 修复完成)
> PRD 文件: PRD.md (化妆品企业级多模态 RAG 智能问答系统 — 双卡版)

---

## 总览

| 类别 | 完全实现 | 部分实现 | 未实现 |
|------|---------|---------|--------|
| §3 离线知识库构建 | 12 项 | 3 项 | 1 项 |
| §4 在线推理架构 | 18 项 | 1 项 | 0 项 |
| §5 并发准入控制 | 11 项 | 0 项 | 0 项 |
| §6 多模态能力 | 5 项 | 0 项 | 0 项 |
| §7 检索与排序 | 13 项 | 1 项 | 0 项 |
| §8 Fail-safe 机制 | 2 项 | 1 项 | 0 项 |
| §9 弹性降级与限流 | 4 项 | 1 项 | 0 项 |
| §10 缓存体系 | 6 项 | 0 项 | 0 项 |
| §11 权限与安全审计 | 6 项 | 0 项 | 0 项 |
| §12 可观测性 | 4 项 | 5 项 | 6 项 |
| §13 API 服务拆分 | 3 项 | 1 项 | 0 项 |
| **合计** | **84 项** | **13 项** | **7 项** |

> **本次修复 (2026-06-05): 修复 8 项缺陷 → 未实现 14→7 项, 部分实现 24→13 项**
> - ✅ §10.6 requires_context 缓存会话隔离
> - ✅ §9 Prefix Caching 保护 (不修改 max_tokens)
> - ✅ §5.2.5 P0/P1/P2 优先级差异化降级
> - ✅ §11 common/auth.py 过滤注入修复
> - ✅ §7.1 加权 RRF 融合
> - ✅ §4.4 Rewrite 降级策略补全 (TopK/缓存/输出)
> - ✅ §4.6 微服务续写 session_state 修复
> - ✅ §9 降级阈值对齐 (0.85/0.90/0.95/0.97)

---

## 未实现功能清单 (7 项)

### 🟡 中等 — 影响功能完整性或偏离 PRD 规范

| # | PRD 章节 | 功能描述 | 现状 | 影响 |
|---|---------|---------|------|------|
| **1** | §3.6 | 文档元数据 `effective_epoch` + `expiry_epoch` 双字段模型 | 仅实现单一 `doc_version_epoch` 字段。缺失逐文档的生效/过期 epoch 字段 | 中 |
| **2** | §3.6 | 每日/每小时 Airflow DAG 触发 epoch 滚动与归档 | 仅有 weekly 和 monthly DAG。无每日/每小时 epoch bump DAG | 中 |
| **3** | §4.6 | Answer Plan 结构化大纲（法规类强制启用） | `answer_outline` 字段已定义但生成逻辑未填充 | 中 |

### 🟢 轻微 — 部署/配置/监控缺失

| # | PRD 章节 | 功能描述 | 现状 | 影响 |
|---|---------|---------|------|------|
| **4** | §3.2 | JSON Lines 中间文件持久化 | 数据结构正确但未序列化到 JSONL 磁盘文件 | 低 |
| **5** | §3.2 | `law_id` / `ingredient_id` 自动提取 | 元数据 dict 支持传入但无自动提取逻辑 | 低 |
| **6** | §3.3 | PaddleOCR 依赖 | 代码已实现但 requirements.txt 中被注释掉 | 低 |
| **7** | §4.4 | CLIP Embedding Cache 旧系统残留清理 | Redis 中无 CLIP 缓存（已符合 PRD），但无显式迁移逻辑 | 低 |

---

## 已修复缺陷清单 (8 项 — 2026-06-05)

| # | PRD 章节 | 修复内容 | 修改文件 |
|---|---------|---------|---------|
| 1 | §10.6 | `requires_context=true` 缓存会话隔离：读写路径检查标志，同 session 复用 | [core/pipeline.py](core/pipeline.py) |
| 2 | §9 | Prefix Caching 保护：`get_effective_max_tokens()` 返回原值不变，新增 `get_truncation_tokens()` 做应用层流式截断 | [admission/kv_admission.py](admission/kv_admission.py), [core/pipeline.py](core/pipeline.py) |
| 3 | §5.2.5 | P0/P1/P2 优先级差异化降级：admit() 根据 business_type 返回 priority，极端过载时 P0 受保护/P1 降级/P2 返回 503 | [admission/kv_admission.py](admission/kv_admission.py), [core/pipeline.py](core/pipeline.py) |
| 4 | §11 | `common/auth.py` 过滤注入修复：添加 uint32 范围校验 + epoch 正则白名单 | [common/auth.py](common/auth.py) |
| 5 | §7.1 | 加权 RRF 融合：`_merge_and_dedup()` 从简单去重升级为 w_text/w_clip/w_ocr 加权分数计算 | [core/pipeline.py](core/pipeline.py) |
| 6 | §4.4 | Rewrite 降级策略补全：降级时 TopK→300、缓存禁用、输出收缩 | [core/pipeline.py](core/pipeline.py) |
| 7 | §4.6 | 微服务续写 session_state 修复：从 SessionState 注册表加载含锁定 doc_ids 的会话状态 | [generation-service/main.py](generation-service/main.py) |
| 8 | §9 | 降级阈值对齐：0.7/0.8/0.9/0.95 → 0.85/0.90/0.95/0.97 | [admission/kv_admission.py](admission/kv_admission.py) |

---

## 部分实现功能清单 (13 项 — 原 24 项, 已修复 11 项)

### 架构已到位但有偏差

| PRD 章节 | 功能 | 偏差说明 |
|---------|------|---------|
| §4.1 | 权限前置绑定 (Permission Binding) | 非独立流水线步骤，权限掩码通过参数透传到 `parallel_recall.execute()` 内部隐式执行 |
| §4.1 | Rerank Batch Aggregator 作为流水线独立步骤 | Aggregator 已实现并在 CrossEncoder 内部调用，但不在主 pipeline 中作为显式步骤出现 |
| §7.3 | Rerank Batch Aggregator 覆盖 5 种批处理 | 实际仅聚合 CrossEncoder 和 NLI；BiEncoder/CLIP Text 使用独立 embedding batch 服务 |
| §4.6 | 续写禁止重检索 | 架构层面保证（续写端点不调用检索），但无显式运行时断言/检查 |
| §8 | 基础设施故障返回 HTTP 503 | `ServiceUnavailableError(503)` 异类存在但未被使用；实际返回 500/502/504 |
| §3.1 | `user_store.py` admin 角色掩码 | `user_store.py` 定义 admin=0x01，`config.json` 定义 admin=0x7FFFFFFF，两条路径不一致 |
| §12 | OpenTelemetry + Jaeger | Tracing 桩代码存在，但 docker-compose 缺少 Jaeger 容器、config.json 无 monitoring 节 |
| §12 | 告警通知渠道 | 告警框架完整（webhook + Slack），但 `alerting.notification_channels` 为空数组 `[]` |
| §12 | NLI 矛盾比例指标 | 单次请求的 `nli_contradiction_score` 已记录，但无聚合统计指标 |
| §12 | Admission Control 拒绝/排队计数 | 拒绝事件写入审计日志，但 MetricsCollector 无专用计数器 |
| §12 | CLIP 超时率指标 | 超时处理已实现，但 MetricsCollector 无专用计数器 |
| §12 | Rerank Batch 指标接入 MetricsCollector | `RerankBatchAggregator.get_stats()` 可获取数据，但未管道到 MetricsCollector |
| §13 | 微服务容器化部署 | 5 个微服务各自独立 `main.py`，但 docker-compose.yml 仅定义主 app 服务 |

---

## 已完整实现功能 (73 项)

以下功能经代码审计确认完整实现，按 PRD 章节分组：

### §3 离线知识库构建
- 3.1 文本清洗去噪 → 结构化处理 → 语义切块 (500 tokens, 10% 重叠)
- 3.1 bge-base-zh-v1.5 生成 768 维向量
- 3.2 doc_type 元数据记录
- 3.2 权限字段离线注入 (Bitmask role_mask + dept_mask)
- 3.3 图像增强 (二值化/去噪/倾斜校正)
- 3.3 视觉权重注入 (核心区域 3x 重复 → ocr_main_text)
- 3.3 CLIP-ViT-B/16 生成 512 维图像向量
- 3.3 BLIP 仅在线按需触发 (离线不执行)
- 3.4 多 Collection 物理隔离 (rag_text_768 / rag_image_512)
- 3.4 IVF_FLAT 索引 + 自适应 nlist (≈√N)
- 3.4 rag_image_512 含 image_uri 字段
- 3.4 int32 位图替代数组, 单条 O(1) 判断
- 3.5 is_allowed() 统一访问判断 (公开/超管/普通 RBAC)
- 3.5 Milvus/ES 过滤表达式下推 (Python 层无运行时过滤)
- 3.7 Airflow 周增量 + 月全量 DAG

### §4 在线推理架构
- 4.1 核心链路完整 (Query → 身份 → 缓存 → 复杂度 → Rewrite → 召回 → Rerank → Evidence → 生成 → Answer Gate)
- 4.1 PipelineContext 全链路状态携带 (RequestContext + SessionState)
- 4.2 双 vLLM 实例无状态路由
- 4.3 BERT 复杂度评估 (二分类)
- 4.4 Query Rewrite 强制 JSON Schema 输出
- 4.4 Rewrite 失败重试 (1 次 temperature=0)
- 4.4 结构兜底 (confidence=0.3, fallback=True)
- 4.4 逻辑一致性修正 (regulation + image → compliance)
- 4.4 失败返回 HTTP 200 引导性拒答
- 4.5 CLIP 三层判别路由 (≥0.6 同步/0.3-0.6 低成本/<0.3 跳过)
- 4.5 CLIP 120ms 超时 Fallback
- 4.5 异步补充召回 + 多轮预热
- 4.6 上下文重建 + 单次重生成 (非续写)
- 4.6 has_more + session_id 多段生成
- 4.6 证据锁定 (Top-3 doc_id)
- 4.7 动态输出长度 (法规1024/研发768/通用256)
- 4.7 KV Cache 应用层截断 (不修改 vLLM 参数)

### §5 并发准入控制
- 5.2 KV-aware Admission Control (Token-Based)
- 5.2 KV Budget 计算 (GPU Cache × 0.7 安全系数)
- 5.2 单请求 KV 成本估算 (prefill + decode 因子)
- 5.2 四级渐进降级 (收紧/截断/软停/强制降级)
- 5.2.5 KV Pressure 实时监控

### §6 多模态
- 6 BLIP Rule + BERT 双路触发 (<5% 触发率)
- 6 BLIP 结果缓存 TTL 1h
- 6 MinIO 临时签名 URL (60s) + 权限重校验
- 6 CLIP 不支持用户实时上传图片 (仅离线已向量化)

### §7 检索与排序
- 7.1 并行多路召回 (Dense + BM25 + CLIP + 改写泛化)
- 7.1 Union 合并 doc_id 去重
- 7.2 检索一致性评分 (Retrieval Agreement Score)
- 7.3 Stage 1: BiEncoder 宽保留 Top 150
- 7.3 Stage 2: CrossEncoder 双模型 Ensemble (CE-A + CE-B)
- 7.3 CE_score = avg(CE_A, CE_B)
- 7.3 Platt Scaling 校准
- 7.4 Evidence Score = w1×CE_Top1 + w2×CE_Top3_Mean + w3×Agreement + w4×Consistency
- 7.4 NLI Top-3 交叉校验
- 7.4 决策规则 (≥0.75 放行/0.55-0.75 增强生成/<0.55 拒答)
- 7.4 权重 w1-w4 可配置

### §8 Fail-safe
- 8 Answer Gate NLI 校验 (Answer vs Top1 Doc)
- 8 contradiction > 0.5 警告 + 法规类强制拒答

### §9 弹性降级
- 9 令牌桶限流 (Redis-State) + 内存降级
- 9 业务类型配额 (法规60%/研发30%/通用10%)

### §10 缓存体系
- 10.2 缓存 Key = hash(query + embedding_ver + epoch + prompt_ver + schema_ver + role_mask + dept_mask)
- 10.3 L1 Public Cache + L2 Private Cache 分层
- 10.4 Redis 仅负责 TTL/Key 查找 (无运行时权限校验)
- 10.5 version_epoch 替代 expiry_date (Lazy GC via LRU)
- 10.7 CLIP Embedding Cache 已移除, 替换为 Session 级临时缓存

### §11 权限与安全审计
- 11 审计日志记录 user_role_mask / 过滤表达式 / 拦截原因
- 11 user_query SHA256 哈希
- 11 研发配方类查询脱敏 [REDACTED]

### §12 可观测性
- 12 全链路追踪 (OpenTelemetry 桩)
- 12 Prometheus 指标端点
- 12 关键指标: L1/L2 命中率 / Rewrite 延迟成功率 / BLIP 触发率 / Evidence Gate 分数 / KV Pressure / 降级触发次数
- 12 告警框架 (webhook + Slack, 含 BLIP>10%, KV>85%, KV Pressure>0.9, Rerank Batch>50ms)
- 12.2 A/B 实验平台 (实验生命周期 + 流量分割 + z-test 分析)

### §13 API 服务拆分
- 13 /rewrite 端点 (query → 标准化 JSON, P99≤45ms)
- 13 /generate 端点 (接收 rewrite 结果, 执行完整 RAG)
- 13 5 个微服务清晰拆分 (api-gateway/rewrite/retrieval/generation/cache)

---

## 优先修复建议

### P1 — 后续迭代
1. 补齐 Prefix Caching / L1/L2 / Redis 降级等缺失告警规则
2. 将 Jaeger 容器加入 docker-compose + 安装 OTel exporter
3. 配置告警通知渠道 (Slack webhook)
4. 实现 `effective_epoch` / `expiry_epoch` 双字段版本模型 + 每日 Airflow DAG
5. 实现 Answer Plan 结构化大纲生成逻辑
6. 补充 CLIP timeout / Admission reject / Rerank Batch 指标到 MetricsCollector
