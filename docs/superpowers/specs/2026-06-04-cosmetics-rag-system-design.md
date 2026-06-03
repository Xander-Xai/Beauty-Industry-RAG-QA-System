# 化妆品企业级多模态 RAG 智能问答系统 — 设计规格书

**版本：** v1.0
**日期：** 2026-06-04
**状态：** 初稿，待用户确认

---

## 一、项目背景与目标

基于已有的汽车知识问答系统代码仓库，进行二次开发，将其改造为**化妆品企业级多模态 RAG 智能问答系统**。

### 核心目标

- 构建统一知识中枢，支持化妆品行业多模态问答（成分/法规/配方/原料/产品）
- 低幻觉、高可信度答案生成
- 细粒度 RBAC 权限控制（研发/品质/法规/销售部门）
- 性能指标：P95 ≤ 2s，P99 ≤ 3s，有效并发 20~25，QPS 12~18

### 改造范围

原项目有两套代码：
- **旧版汽车系统**（根目录 `app.py` → `agent_module.py`）：Flask + ChatGLM3-6B + FAISS → **废弃**
- **新版化妆品管线**（`core/`、`rewrite/`、`router/` 等）：分模块设计 → **保留并完成**

**本次改造聚焦新版管线的完善和上线，旧版代码整体废弃。**

---

## 二、系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Web 层（FastAPI + Docker Compose）            │
│  /query (POST)   /chat (WebSocket)   /admin/stats   /health         │
└──────────┬────────────────┬──────────────────┬────────────────────────┘
           │                │                  │
┌──────────▼────────────────▼──────────────────▼──────────────────────┐
│                      在线推理管线（OnlineRAGPipeline）               │
│                                                                     │
│  请求入口 → 身份解析 → L1/L2 缓存                                    │
│           MISS → 复杂度评估 → Query Rewrite → 权限绑定               │
│               → 双 Embedding 路由 → 并行多路召回                    │
│               → Union 去冗 → BiEncoder(宽保留)                       │
│               → CrossEncoder Ensemble → Evidence Gate               │
│               → LLM 生成 → Answer Gate                              │
└──────────┬──────────────────────────┬───────────────────────────────┘
           │                          │
┌──────────▼───────────┐  ┌──────────▼───────────────────────────────┐
│    GPU0 (生成推理)     │  │         GPU1 (控制与轻推理)               │
│  Qwen3-14B + QLoRA    │  │  vLLM-Rewrite(4B) / vLLM-Gen-4B(4B)     │
│  (vLLM, port 8100)    │  │  BERT复杂度分类 / CLIP / BiEncoder       │
│                       │  │  CrossEncoder Ensemble / NLI / BLIP       │
└───────────────────────┘  └──────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────────────┐
│                      知识库层                                        │
│  Milvus (rag_text_768 + rag_image_512)                               │
│  Elasticsearch (BM25 关键词检索)                                      │
│  Redis (L2 缓存 + 会话状态)                                          │
└─────────────────────────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────────────┐
│                      离线知识库构建                                   │
│  DocumentProcessor → ImageProcessor → Vectorizer → Scheduler         │
│  (PDF/Word/Excel/图片 → 文本块 → OCR → BGE/CLIP → Milvus+ES)          │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 三、模块分工与状态

| 模块 | 文件路径 | 状态 | 说明 |
|------|---------|------|------|
| **HTTP 服务层** | `app.py`（改造） | 🔴 **需新建** | FastAPI 服务，替代 Flask |
| **管线编排** | `core/pipeline.py` | ✅ 已完成 | 13 步管线编排器 |
| **上下文管理** | `core/pipeline_context.py` | ✅ 已完成 | 请求/会话状态 |
| **异常定义** | `core/exceptions.py` | ✅ 已完成 | 各类异常与降级标记 |
| **无状态路由** | `router/stateless_router.py` | ✅ 已完成 | vLLM 实例分发 |
| **Query Rewrite** | `rewrite/query_rewriter.py` | ⚠️ 需修订 | Prompt 需化妆品化；需接 vLLM |
| **KV 准入控制** | `admission/kv_admission.py` | ✅ 已完成 | Token 级并发控制 |
| **复杂度评估** | `models/complexity_evaluator.py` | ⚠️ 需修订 | 关键词需化妆品化 |
| **Embedding 服务** | `models/embedding_service.py` | ⚠️ 需修订 | config.json 编码问题 |
| **LLM 客户端** | `models/llm_client.py` | ✅ 已完成 | 需接 vLLM |
| **并行多路召回** | `retrieval/parallel_recall.py` | ✅ 已完成 | 4 路并行召回 |
| **Dense 检索器** | `retrieval/dense_retriever.py` | ✅ 已完成 | BGE + Milvus |
| **BM25 检索器** | `retrieval/bm25_retriever.py` | ✅ 已完成 | ES BM25 |
| **CLIP 检索器** | `retrieval/clip_retriever.py` | ✅ 已完成 | CLIP + Milvus |
| **BiEncoder 重排** | `retrieval/bi_encoder.py` | ✅ 已完成 | 宽保留 Top150 |
| **CrossEncoder 重排** | `retrieval/cross_encoder_ensemble.py` | ✅ 已完成 | 双模型 Ensemble |
| **Evidence Gate** | `retrieval/evidence_gate.py` | ✅ 已完成 | 多维度投票 |
| **Answer Gate** | `retrieval/answer_gate.py` | ✅ 已完成 | NLI 答案校验 |
| **Rerank 批聚合** | `retrieval/rerank_batch_aggregator.py` | ✅ 已完成 | GPU 微批聚合 |
| **L1/L2 缓存** | `cache/redis_cache.py` | ⚠️ 需修订 | config.json 引用 |
| **RBAC 权限** | `auth/bitmask_rbac.py` | ✅ 已完成 | Bitmask 权限控制 |
| **文档处理** | `offline/document_processor.py` | ✅ 已完成 | PDF/Word/Excel/TXT |
| **图像处理** | `offline/image_processor.py` | ✅ 已完成 | PaddleOCR + CLIP |
| **向量化写入** | `offline/vectorizer.py` | ✅ 已完成 | Milvus + ES 写入 |
| **离线调度** | `offline/scheduler.py` | ✅ 已完成 | 增量/全量更新 |
| **可观测性** | `monitoring/otel_tracer.py` | ✅ 已完成 | OTel + Metrics + Alerting |
| **配置文件** | `config.json` | 🔴 **需重写** | 编码损坏，内容需对齐化妆品系统 |

---

## 四、需要实现的核心功能

### 4.1 HTTP 服务层（新建 FastAPI）

- **POST `/query`**：单轮问答（JSON body: `{query, session_id?, user_id?}`)
- **WebSocket `/ws/chat/{session_id}`**：多轮对话
- **GET `/health`**：健康检查（检查 vLLM、Milvus、ES、Redis 连接）
- **GET `/stats`**：系统统计（缓存命中率、各阶段延迟百分位、Evidence Gate 分数分布）
- **中间件**：请求日志、CORS、认证（从请求头提取 user_role_mask / user_dept_mask）

### 4.2 config.json 重写

- 修复编码问题（移除反斜杠转义，还原正常 JSON）
- 内容对齐化妆品系统（ES index → `cosmetics_docs`）
- 补充缺失字段：`embedding_model_path`、`llm_path`、`vector_dbindex_path`（供旧模块兼容）

### 4.3 Query Rewrite 模块修订

- Prompt 模板从「化妆品行业知识问答系统」改为实际业务场景
- 实现 `_call_llm` 方法，接入 `StatelessRouter.route_rewrite()`
- `business_type` 扩展化妆品场景：`regulation`（法规）/ `development`（研发）/ `ingredient`（成分）/ `product`（产品）/ `general`（通用）

### 4.4 系统启动与依赖检查

- 启动时检查所有外部依赖（vLLM、Milvus、ES、Redis）是否可用
- 不可用时输出 warning 而非崩溃（各模块有独立降级逻辑）

### 4.5 前端界面（新增）

- React/Vue 单页应用，调用 FastAPI
- 支持：文本输入、图片上传（多模态）、对话历史、回答溯源（显示引用的证据文档）

---

## 五、数据流

### 5.1 在线查询流程

```
1. 用户发送 query → FastAPI 解析请求
2. 身份解析：从请求头 / token 提取 user_role_mask、user_dept_mask
3. 缓存查询：L1（内存）/ L2（Redis），命中则返回
4. KV 准入控制：检查 GPU KV 压力，超限则排队或拒绝
5. 复杂度评估：BERT 二分类 → 简单路由到 4B，复杂路由到 14B
6. Query Rewrite：识别业务类型、意图，提取标准化实体
7. 双 Embedding：同步评估 CLIP 是否参与（is_visual_relevant ≥ 0.3）
8. 并行 4 路召回（Dense/BM25/CLIP/Rewrite变体）→ Union 去冗
9. BiEncoder 宽保留（Top150）
10. CrossEncoder Ensemble 双模型精排（Top10）
11. Evidence Gate 投票决策
12. LLM 生成（按复杂度路由）→ Answer Gate NLI 校验
13. 写入缓存 → 返回结果
```

### 5.2 离线知识库构建流程

```
1. 扫描 data_dir 中的文档和图片
2. 文档处理：PDF/Word/Excel/TXT → 文本提取 → 清洗 → 语义切块（500 tokens，10% 重叠）
3. 图像处理：PaddleOCR → 视觉权重注入 → CLIP 特征提取
4. 向量化：BGE（768d）→ Milvus rag_text_768；CLIP（512d）→ Milvus rag_image_512
5. 权限标签注入：role_mask / dept_mask（Bitmask）
6. 同步写入 ES（BM25 索引）
7. 文档生命周期管理：active_epoch 版本化，archived 状态标记
```

---

## 六、技术选型

| 组件 | 选型 | 说明 |
|------|------|------|
| HTTP 框架 | **FastAPI** | async 支持，生产级 |
| LLM 推理 | **vLLM** | continuous batching，高吞吐 |
| 向量数据库 | **Milvus** | 多 Collection，支持动态扩容 |
| 全文检索 | **Elasticsearch** | BM25，ES 8.x |
| 缓存 | **Redis** | L2 缓存 + 会话状态 |
| 文本向量 | **BGE-base-zh-v1.5** | 768d，中文优化 |
| 图像向量 | **CLIP-ViT-B/16** | 512d，图文对齐 |
| 图像 OCR | **PaddleOCR** | 中文支持，降级到 OpenCV |
| LLM 模型 | **Qwen3-14B + QLoRA**（复杂）/ **Qwen3-4B**（简单） | 路由决策 |
| CrossEncoder | **cross-encoder-law + cross-encoder-base** | 双模型 Ensemble |
| NLI | **deberta-v3-base-nli** | 答案校验 |
| 部署 | **Docker Compose** | vLLM + Milvus + ES + Redis + FastAPI |

---

## 七、数据模型

### 7.1 Milvus Collections

**rag_text_768**（768 维）：
- `id` (INT64, PK, auto_id)
- `doc_id` (VARCHAR 128)
- `content` (VARCHAR 65535)
- `embedding` (FLOAT_VECTOR 768)
- `doc_type` (VARCHAR 64) — regulation / development / ingredient / product
- `embedding_type` (VARCHAR 32) — text / image_ocr
- `role_mask` (INT32)
- `dept_mask` (INT32)
- `doc_version_epoch` (VARCHAR 32)
- `status` (VARCHAR 16) — active / archived

**rag_image_512**（512 维）：
- `id` (INT64, PK, auto_id)
- `doc_id` (VARCHAR 128)
- `ocr_text` (VARCHAR 65535)
- `embedding` (FLOAT_VECTOR 512)
- `image_uri` (VARCHAR 512)
- `role_mask` / `dept_mask` / `doc_version_epoch` / `status`

### 7.2 RBAC Bitmask

```
角色：admin=2147483647, rd=1, quality=2, regulation=4, sales=8
部门：all=0, rd_dept=1, quality_dept=2, regulation_dept=4, sales_dept=8
超级管理员：0xFFFFFFFF
```

---

## 八、待废弃的旧代码

以下文件属于旧版汽车系统，与化妆品系统无关，将被废弃：

| 文件 | 原因 |
|------|------|
| `agent_module.py` | 旧版调度器，架构已废弃 |
| `rag_module.py` | 旧版检索，FAISS 单机版 |
| `knowledge_graph.py` | 汽车领域，维基百科搜索 |
| `multimodal_module.py` | 旧版图像处理，BLIP 独立调用 |
| `vectorize_and_index.py` | 旧版向量化，与新版 offline/ 重复 |
| `finetune_qlora.py` | 旧版 ChatGLM3 微调 |
| `templates/` + `static/` | 旧版 Flask 前端，将被新前端替代 |
| `data/questions.json` | 汽车问答数据 |
| `data/finetune_data.json` | 汽车微调数据 |
| `chatglm3-6b/` | 旧模型目录 |
| `readme.md` + `rule.txt` | 改造设计文档，已在本文档中整合 |

---

## 九、验收标准

1. **服务可启动**：FastAPI 服务成功监听，`/health` 返回 `{"status": "healthy"}`
2. **缓存可工作**：Redis 连接正常，L1/L2 缓存读写正常
3. **管线可跑通**：即使外部依赖不可用，系统也能优雅降级并返回合理响应
4. **Docker Compose 可用**：一条命令启动所有服务（vLLM、Milvus、ES、Redis、FastAPI）
5. **性能达标**：单次查询 P95 ≤ 2s（依赖 vLLM 推理速度）
6. **旧代码清理**：废弃文件已移除或重命名

---

## 十、优先级排序（实施计划参考）

**P0（核心可用）**：
1. 重写 `config.json`（修复编码）
2. 重写 `app.py`（FastAPI 服务层）
3. 修订 `rewrite/query_rewriter.py`（接 vLLM）
4. 修订 `models/complexity_evaluator.py`（化妆品关键词）
5. 修订 `cache/redis_cache.py`（配置对齐）
6. 清理废弃旧代码
7. 编写 Docker Compose 配置

**P1（功能完善）**：
8. 实现前端 Web UI
9. 补充示例化妆品数据（demo）

**P2（优化监控）**：
10. OTel 接入（Jaeger）
11. Prometheus + Grafana 监控面板