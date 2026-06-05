# PRD 补全实施计划

> 基于 PRD 文档与代码库逐项比对，补全所有未实现/部分实现的功能。
> 创建日期: 2026-06-05
> 最后更新: 2026-06-05 (Phase 1-4 全部完成)

---

## 实施状态

### Phase 1: P0 — 上线阻塞项 ✅ 已完成

| # | 功能 | 状态 | 修改文件 |
|---|------|------|----------|
| 1 | 离线管线权限注入 | ✅ | config.json, offline/scheduler.py |
| 2 | MinIO 临时签名 URL | ✅ | common/minio_client.py (新增), api-gateway/routers/generation.py, requirements.txt |

### Phase 2: P1 — 核心功能补全 ✅ 已完成

| # | 功能 | 状态 | 修改文件 |
|---|------|------|----------|
| 3 | 图像预处理管线（二值化/去噪/纠偏） | ✅ | offline/image_processor.py |
| 4 | OCR 文本 BGE 向量化接入 | ✅ | offline/scheduler.py |
| 5 | Retrieval Agreement Score | ✅ | retrieval/parallel_recall.py, core/pipeline.py |
| 6 | CLIP 异步补召回机制 | ✅ | retrieval/parallel_recall.py |

### Phase 3: P2 — 质量提升 ✅ 已完成

| # | 功能 | 状态 | 修改文件 |
|---|------|------|----------|
| 7 | KV 渐进降级（四级阈值） | ✅ | admission/kv_admission.py, generation-service/kv_admission.py |
| 8 | CLIP 三档路由阈值 | ✅ | core/pipeline.py |
| 9 | Answer Plan 结构化增强 | ✅ | core/pipeline.py |
| 10 | Prefix Caching 保护 | ✅ | admission/kv_admission.py, core/pipeline.py |
| 11 | Redis-State 限流 | ✅ | api-gateway/middleware/rate_limiter.py |

### Phase 4: P3 — 长期建设 ✅ 已完成

| # | 功能 | 状态 | 修改文件 |
|---|------|------|----------|
| 12 | Apache Airflow 定时调度 DAG | ✅ | dags/knowledge_base_dags.py (新增) |
| 13 | 离线反馈闭环 | ✅ | offline/feedback_loop.py (新增) |
| 14 | Query Rewrite 反馈闭环 | ✅ | rewrite/feedback.py (新增) |
| 15 | A/B 实验平台 | ✅ | common/ab_testing.py (新增) |

---

## 变更文件清单

```
 新增文件
+  common/minio_client.py          ← MinIO 临时签名 URL 客户端
+  common/ab_testing.py            ← A/B 实验平台基础框架
+  dags/knowledge_base_dags.py     ← Apache Airflow DAG 定时调度
+  offline/feedback_loop.py        ← 离线反馈闭环（日志采样 + 参数更新）
+  rewrite/feedback.py             ← Query Rewrite 反馈闭环
+  docs/PRD_IMPL_PLAN.md           ← 本计划文档

 修改文件
~  config.json                     ← 新增 permission_rules / minio / clip_async 配置
~  requirements.txt                ← 新增 minio>=7.2.0
~  run_offline.py                  ← 新增 feedback / rewrite-feedback 模式
~  offline/scheduler.py            ← 权限注入 + OCR BGE 向量化
~  offline/image_processor.py      ← 图像预处理管线（二值化/去噪/纠偏）
~  core/pipeline.py                ← CLIP 三档路由 / Retriever Agreement / Answer Plan / 准入适配
~  retrieval/parallel_recall.py    ← Retrieval Agreement Score + CLIP 异步补召回
~  admission/kv_admission.py       ← 四级渐进降级 + 应用层截断 + 强制降级
~  generation-service/kv_admission.py  ← 同上
~  api-gateway/routers/generation.py   ← MinIO 签名 URL 接入 /media 端点
~  api-gateway/middleware/rate_limiter.py ← Redis-State 令牌桶 + 内存降级
```

---

## 详细实施规范

### 1. 离线管线权限注入 ✅

**目标**: offline/scheduler.py 不再硬编码 role_mask=0, dept_mask=0

**方案**:
- config.json 新增 `permission_rules` 配置段，支持按路径模式映射权限
- scheduler.py 新增 `_resolve_permission(fpath)` 方法，使用 fnmatch 匹配
- 匹配规则: **/法规/** → regulation (4,4), **/研发/** → rd (1,1), etc.

**涉及文件**: config.json, offline/scheduler.py

### 2. MinIO 临时签名 URL ✅

**目标**: /api/media/{doc_id} 返回 60s 临时签名 URL + 权限二次校验

**方案**:
- 新增 common/minio_client.py 封装 MinIO SDK 连接和 presigned_url 生成
- api-gateway /media 端点: RBAC 校验 → MinIO 签名 URL → 返回
- MinIO 不可用时优雅降级为空 URL（调用方使用代理路径）

**涉及文件**: common/minio_client.py (新增), api-gateway/routers/generation.py, requirements.txt

### 3. 图像预处理管线 ✅

**目标**: PaddleOCR 前增加二值化/去噪/纠偏

**方案**:
- image_processor.py 新增 `_preprocess_image()` 方法
- 处理链: RGB → grayscale → fastNlMeansDenoising (h=10) → Otsu 二值化 → HoughLines 纠偏
- `_correct_skew()`: 概率 Hough 变换检测主方向，< 0.5° 跳过避免伪旋转
- process_image() 改为: 加载 → 预处理 → OCR（使用增强后的图像）

**涉及文件**: offline/image_processor.py

### 4. OCR 文本 BGE 向量化 ✅

**目标**: 图像 OCR 文本通过 BGE 生成 768d 向量写入 rag_text_768

**方案**:
- scheduler.py 处理图像后，对非空 ocr_main_text 调用 vectorize_and_store_text()
- embedding_type 设为 "image_ocr"，使 OCR 文本可通过 BGE 密集语义检索召回

**涉及文件**: offline/scheduler.py

### 5. Retrieval Agreement Score ✅

**目标**: 计算多路召回的语义一致性分数

**方案**:
- parallel_recall.py 新增 `_compute_agreement_score(path_results)` 方法
- 各路径 Top-10 文档 ID 集合的两两 Jaccard 相似度均值
- execute() 返回值从 list 改为 tuple[list, float]
- pipeline.py 直接从 execute() 获取 agreement_score，无需单独计算

**涉及文件**: retrieval/parallel_recall.py, core/pipeline.py

### 6. CLIP 异步补召回 ✅

**目标**: 后台异步 TopK=100 CLIP 召回 + 多轮预热

**方案**:
- parallel_recall.py 新增 `recall_async_clip()` 方法
- 多轮预热: 检查 SessionState.async_clip_results 是否有可复用结果
- 新结果存入 session 供后续轮次使用
- config.json 新增 clip_async 配置段

**涉及文件**: retrieval/parallel_recall.py, config.json

### 7. KV 渐进降级（四级阈值） ✅

**目标**: 实现完整的 0.7→0.8→0.9→0.95 四级渐进降级

**方案**:
```
THRESHOLD_TIGHTEN   = 0.7  → reason='admitted_with_tighten'  (token bucket 收紧)
THRESHOLD_TRUNCATE  = 0.8  → reason='admitted_with_truncation' (应用层截断 max_tokens/2)
THRESHOLD_SOFT_STOP = 0.9  → reason='soft_stop' (软拒绝)
THRESHOLD_CRITICAL  = 0.95 → reason='critical' (强制降级 14B→4B + max_tokens/4)
```
- 新增 `get_effective_max_tokens()`: 根据 reason 返回截断后的 token 数
- 新增 `should_force_downgrade()`: 判断是否需要 14B→4B 降级

**涉及文件**: admission/kv_admission.py, generation-service/kv_admission.py

### 8. CLIP 三档路由 ✅

**目标**: ≥0.6 全量同步, 0.3-0.6 低成本同步, <0.3 跳过

**方案**:
- `_should_use_clip_sync()` 返回值从 bool 改为 tuple[bool, int] (use_clip, top_k)
- score >= 0.6 → (True, 50) 全量同步
- 0.3 <= score < 0.6 → (True, 20) 低成本同步
- score < 0.3 → (False, 0) 跳过

**涉及文件**: core/pipeline.py

### 9. Answer Plan 结构化增强 ✅

**目标**: 二阶段生成（首段 + 续写填充）

**方案**:
- pipeline.py 新增 `_try_structured_continuation()` 方法
- 当首段生成被截断 (has_more=True) 时，自动触发二阶段
- 通过 `generate_continuation()` 补充剩余内容，合并两段

**涉及文件**: core/pipeline.py

### 10. Prefix Caching 保护 ✅

**目标**: 降级时不动 max_tokens

**方案**:
- KV 降级截断在应用层实现（get_effective_max_tokens 返回建议值）
- vLLM 请求参数中的 max_tokens 始终保持不变
- pipeline.py 使用 `effective_max_tokens` 替代 `ctx.max_output_tokens`

**涉及文件**: admission/kv_admission.py, core/pipeline.py

### 11. Redis-State 限流 ✅

**目标**: 将内存级令牌桶升级为 Redis-backed

**方案**:
- Redis Lua 脚本令牌桶（原子操作，多实例一致性）
- Redis 不可用时自动降级为内存级令牌桶
- 保持原有 QPS 配置: regulation=60, research=30, general=10

**涉及文件**: api-gateway/middleware/rate_limiter.py
