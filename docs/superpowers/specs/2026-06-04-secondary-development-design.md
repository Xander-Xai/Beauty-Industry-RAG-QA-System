# 化妆品 RAG 系统二次开发设计文档

> **日期**: 2026-06-04
> **目标**: 将代码完整的 RAG 系统从"代码就绪"推进到"生产可用"
> **方案**: 自底向上补全（方案 A），6 个 Phase，2-3 个月

---

## 1. 背景与目标

### 1.1 现状

代码库已完整实现 PRD 定义的所有核心模块：

| 模块 | 状态 | 说明 |
|------|------|------|
| core/pipeline.py | ✅ 456 行 | 18 步 RAG 管线编排器 |
| api/ | ✅ 4 端点 | query, chat, health, stats |
| retrieval/ | ✅ 8 组件 | 4 路召回 + BiEncoder + CrossEncoder + EvidenceGate + AnswerGate |
| rewrite/ | ✅ 完整 | vLLM 接入 + 降级兜底 |
| admission/ | ✅ 完整 | Token-level KV 准入控制 |
| cache/ | ✅ 完整 | L1 内存 + L2 Redis, 权限绑定 Key |
| auth/ | ✅ 完整 | Bitmask RBAC + Milvus 过滤下推 |
| offline/ | ✅ 完整 | 文档/图像处理 + 向量化 + 调度 |
| monitoring/ | ✅ 完整 | OTel + Metrics + Alerting |
| 微服务 (6个) | ✅ 完整 | Gateway + 5 独立服务 |
| tests/ | ✅ 8 个文件 | 单元测试覆盖核心模块 |
| agent_module/ | ⚠️ 遗留 | 汽车领域 NER，未集成 |
| pipeline/, generation/ | ❌ 空包 | 占位，无实际代码 |

### 1.2 差距分析

从"代码完整"到"生产可用"的差距：

- **无真实数据**: 缺少 Mock 数据用于测试验证
- **无模型部署验证**: 代码中的模型调用未在真实 GPU 环境验证
- **无单卡降级**: PRD 假设双卡，缺少单卡测试路径
- **无前端界面**: 企业内部用户需要 Web 交互界面
- **安全未落地**: JWT/审计/脱敏等安全机制仅在 common/ 中有基础实现
- **无压测基线**: 缺少性能验证和调优数据
- **无监控面板**: Metrics 采集已有，但缺少可视化和告警通知对接
- **遗留代码**: agent_module/ 等无用模块需清理

### 1.3 二次开发目标

1. **核心管线跑通**: 从数据导入到问答输出的端到端验证
2. **部署与运维**: Docker 一键启动、模型管理、环境自动化
3. **数据与测试**: Mock 数据、压测脚本、回归测试、性能基线
4. **安全加固**: JWT 认证、审计日志、数据脱敏、HTTPS

### 1.4 约束条件

- **硬件**: GPU 未确定，先单卡测试，目标双卡部署
- **用户**: 企业内部用户（研发/品质/法规/销售）
- **部署**: 先本地跑通，后续上云
- **时间**: 2-3 个月

---

## 2. 总体架构

### 2.1 四层架构

```
L4 · 接入层    FastAPI 路由 + JWT 认证 + RBAC + 前端 Web UI
L3 · 推理层    RAG Pipeline + Rewrite + LLM Generation + Evidence Gate
L2 · 检索层    Milvus/ES 并行召回 + BiEncoder + CrossEncoder + NLI
L1 · 基础设施  vLLM (GPU) + Redis + Milvus + ES + MinIO + Mock 数据
```

### 2.2 Phase 路线图

| Phase | 名称 | 周期 | 核心产出 |
|-------|------|------|----------|
| Phase 1 | 基础设施搭建 | Week 1-2 | Mock 数据、单卡降级、vLLM 配置、遗留清理 |
| Phase 2 | 端到端管线打通 | Week 3-4 | 真实模型接入、集成测试、性能基线 |
| Phase 3 | 部署自动化 | Week 5 | Docker 优化、一键启动、健康检查 |
| Phase 4 | 安全与前端 | Week 6-7 | JWT 认证、审计日志、Web 聊天界面 |
| Phase 5 | 压测与调优 | Week 8-9 | Locust 压测、KV 调优、权重校准 |
| Phase 6 | 生产就绪 | Week 10-12 | 监控面板、文档、故障演练、上线 Checklist |

---

## 3. Phase 1: 基础设施搭建

### 3.1 Mock 数据生成器

新增 `data/mock_generator.py`，生成覆盖 PRD 场景的测试数据：

| 数据类型 | 数量 | 格式 | 说明 |
|----------|------|------|------|
| 法规文档 | 100+ 份 | PDF/Word | 覆盖 8 大法规体系 (GB/T, QB/T 等) |
| 成分数据 | 500+ 条 | JSON | 含安全信息、INCI 对照、限量清单 |
| 配方数据 | 250 个 | Excel | 护肤/彩妆/洗护三大类 |
| 图像数据 | 100 张 | JPG/PNG | 产品包装、成分标签、检测报告 |
| 元数据 | 287+ 条 | JSONL | 含 role_mask, dept_mask, doc_version_epoch |

### 3.2 单卡降级模式

通过 `DEPLOYMENT_MODE` 环境变量切换三种部署模式：

| 模式 | GPU0 | GPU1 | Rerank | 适用场景 |
|------|------|------|--------|----------|
| production | Qwen3-14B Gen | 4B Rewrite + 4B Gen | GPU Batch | 生产环境 |
| testing | Qwen3-4B (复用) | 无 | CPU ONNX | 测试验证 |
| development | 无 | 无 | 模拟 | 纯逻辑验证 |

实现方式：
- `common/config.py` 增加 `deployment_mode` 字段
- `run_services.py` 根据模式选择启动的服务列表
- `models/llm_client.py` 支持单卡模式下 Rewrite 和 Gen 复用同一 vLLM 实例
- `retrieval/rerank_batch_aggregator.py` 增加 CPU ONNX Runtime fallback
- 启动时自动检测 `nvidia-smi`，不符则警告并建议降级

### 3.3 遗留代码清理

**删除**:
- `agent_module/` — 汽车领域遗留 NER，与化妆品管线无关联
- `pipeline/__init__.py` — 空占位包，实际逻辑在 `core/pipeline.py`
- `generation/__init__.py` — 空占位包，实际逻辑在 `models/llm_client.py`

**替换**:
- `data/data.pdf`, `data/image1.jpg`, `data/text_mapping.json` — 替换为 Mock 数据

### 3.4 vLLM 配置适配

- 模型权重路径从硬编码改为环境变量引用 `${MODEL_DIR}`
- config.json 增加 `deployment_mode` 配置节
- 启动时校验 GPU 数量与配置匹配度，不匹配则自动降级并输出警告日志

---

## 4. Phase 2: 端到端管线打通

### 4.1 模型验证矩阵

每个模型有独立的验证方式和通过标准：

| 模型 | 用途 | 验证方式 | 通过标准 |
|------|------|----------|----------|
| Qwen3-14B | 复杂问题生成 | 100 条法规问答 | P95 ≤ 2s |
| Qwen3-4B | 简单生成 + Rewrite | JSON 合规率 | 解析成功率 ≥ 98% |
| BGE-base-zh | 文本向量化 768d | 检索 Recall@50 | ≥ 0.85 |
| CLIP-ViT-B/16 | 图像向量化 512d | 图像检索 Top-10 相关率 | ≥ 0.70 |
| BERT 复杂度 | 简单/复杂分类 | 标注集准确率 | ≥ 95% |
| CrossEncoder ×2 | 精排 | Rerank NDCG@10 | ≥ 0.75 |
| NLI (DeBERTa) | 答案校验 | 矛盾检测 F1 | ≥ 0.80 |
| BLIP | 图像描述 | 触发率 + 延迟 | < 5%, ≤ 120ms |

### 4.2 端到端测试场景

总计 65 条测试用例：

- **基础场景 (20 条)**: 单轮成分查询、法规咨询、产品查询
- **复杂场景 (20 条)**: 多轮对话、跨域问题、模糊意图、图像相关
- **边界场景 (15 条)**: 领域外问题、权限受限、超长输入、并发请求
- **降级场景 (10 条)**: Rewrite 失败、Milvus 不可用、KV 过载、Redis 不可用

### 4.3 性能基线

Phase 2 结束时必须达成：

| 指标 | 目标值 | 测量方式 |
|------|--------|----------|
| 端到端 P95 延迟 | ≤ 3.0s (单卡 ≤ 4.0s) | 55 条测试集 |
| 回答可用率 | ≥ 80% | 人工评估 50 条 |
| 拒答准确率 | ≥ 90% | 边界测试集 |
| 缓存命中率 | L2 ≥ 60% | 循环测试 |
| 单卡 QPS | ≥ 5 | 并发压测 |

---

## 5. Phase 3: 部署自动化

### 5.1 Docker Compose 重构

拆分为多文件覆盖架构：

```
docker-compose.yml          # 主编排 (基础设施)
docker-compose.gpu.yml      # GPU 服务覆盖 (双卡)
docker-compose.cpu.yml      # CPU 降级覆盖 (开发)
```

基础设施服务: app, redis, milvus-standalone, elasticsearch, minio, etcd
GPU 服务: vllm-gen-14b (GPU0), vllm-gen-4b (GPU1), vllm-rewrite (GPU1)

### 5.2 模型管理

新增 `scripts/download_models.sh`:
- 支持 HuggingFace / ModelScope 镜像源切换
- SHA256 校验完整性
- 断点续传
- 模型目录统一挂载，Docker Volume 持久化

### 5.3 一键启动

新增 `scripts/start.sh`，6 步自动化流程：
1. 检查环境 (Docker, nvidia-smi)
2. 检查模型权重完整性
3. 初始化向量库 Schema
4. 导入 Mock 数据 (首次)
5. 启动基础设施 (Redis/Milvus/ES)
6. 启动应用服务

### 5.4 健康检查

| 检查项 | 端点 | 超时 | 失败行为 |
|--------|------|------|----------|
| Redis | GET /health/redis | 2s | 降级到 L1 内存缓存 |
| Milvus | GET /health/milvus | 3s | 切换 ES Fallback |
| ES | GET /health/es | 3s | BM25 路径降级 |
| vLLM | GET /health/vllm | 30s | 启动阶段等待；运行阶段 503 |
| GPU | nvidia-smi | 5s | 自动降级到 CPU 模式 |

---

## 6. Phase 4: 安全加固与前端界面

### 6.1 JWT 认证

基于已实现的 `common/auth.py`，完善为生产级方案：
- RS256 签名（公私钥分离）
- Access Token 15min + Refresh Token 7d
- 角色注入: admin / rd / quality / regulation / sales
- 部门注入: dept_mask 位运算

### 6.2 审计日志

- 记录: user_id, query_hash, 过滤表达式, 拦截原因
- 查询脱敏: user_query → SHA256 哈希
- 配方查询: 敏感内容替换为 [REDACTED]
- 存储: 结构化 JSON → Elasticsearch
- 保留周期: 180 天

### 6.3 传输安全

- HTTPS (TLS 1.3) 通过 Nginx 反向代理
- CORS 白名单 (非通配符)
- Rate Limiting: 法规 60%, 研发 30%, 闲聊 10%
- 请求大小限制: 10KB/query

### 6.4 数据安全

- MinIO 文档: 临时签名 URL (60s 有效)
- 端点内权限重校验 (is_allowed)
- Redis 密码 + TLS
- 环境变量敏感值加密

### 6.5 前端 Web 界面

推荐方案: React + Ant Design + 自定义 Chat 组件 (或基于 ChatGPT-Next-Web 魔改)
- 多轮对话 + Markdown 渲染
- 引用溯源展示 (检索到的文档片段)
- 角色选择器 (模拟不同权限用户)
- 响应式设计
- 部署: Nginx 静态资源 + 反向代理 API

### 6.6 用户管理

- 用户表: PostgreSQL, 存储 role_mask + dept_mask (开发阶段可用 SQLite 替代)
- 管理员 Web UI: 角色分配和部门管理
- 可选 LDAP 集成: 对接企业 AD/LDAP
- 会话管理: 6 轮对话上下文, session_id 关联

---

## 7. Phase 5: 压测与性能调优

### 7.1 Locust 压测方案

三档压测场景:

| 场景 | 并发范围 | 持续时间 | 验证目标 |
|------|----------|----------|----------|
| 常态负载 | 10 → 25 | 30 分钟 | QPS 12-18, P95 ≤ 3s |
| 峰值压力 | 25 → 40 | 10 分钟 | 无 503, 降级生效 |
| 极端过载 | 40 → 80 | 5 分钟 | 无 OOM, P0 保持服务 |

### 7.2 调优维度

**KV Cache 调优**:
- max_num_seqs 动态调整
- gpu_memory_utilization 微调 (0.80-0.90)
- safety_factor 校准 (0.65-0.75)
- Prefix Caching 命中率监控

**检索权重校准**:
- RRF 权重网格搜索: w_text ∈ {0.5, 0.7, 1.0}, w_clip ∈ {0.3, 0.5, 1.0, 2.0}, w_ocr ∈ {0.3, 0.5, 0.7}
- Evidence Gate 阈值: ROC 曲线优化 F2 分数
- CrossEncoder 温度参数
- BiEncoder TopK 截断 (100-200)

**Rerank Batch 优化**:
- 窗口时间: 10ms-30ms 调优
- Batch Size: 32-64 动态填充
- GPU 利用率目标: 60-80%
- 排队延迟 P99: ≤ 50ms

**延迟瓶颈分析**:
- 全链路 Tracing (OpenTelemetry)
- 每阶段延迟占比分析
- 网络延迟: Milvus / ES / Redis
- LLM Decode vs Prefill 拆分

### 7.3 性能目标

| 指标 | 双卡目标 | 单卡目标 | PRD 要求 |
|------|----------|----------|----------|
| P95 延迟 | ≤ 2.0s | ≤ 4.0s | 1.5-3.0s |
| 有效并发 | 20-25 | 8-12 | 20-25 |
| QPS | 12-18 | 3-6 | 12-18 |
| 回答可用率 | ≥ 85% | ≥ 80% | — |
| Prefix Cache 命中 | ≥ 40% | ≥ 20% | — |

---

## 8. Phase 6: 生产就绪

### 8.1 Grafana 监控面板

**系统概览面板**: QPS 实时曲线, P50/P95/P99 延迟, 活跃请求, KV Cache 压力, 错误率
**RAG 质量面板**: L1/L2 缓存命中率, Rewrite 成功率, Evidence Gate 分数分布, NLI 矛盾比例
**资源监控面板**: GPU 显存/利用率, Redis 命中/内存, Milvus 查询延迟, ES 索引/QPS
**告警规则**: KV Pressure > 0.9, P95 > 4s, 错误率 > 5%, Redis 降级 > 5min, Rerank 排队 > 50ms

### 8.2 文档清单

| 文档 | 受众 | 内容 |
|------|------|------|
| 部署手册 | 运维 | 环境准备、安装步骤、配置说明、常见问题 |
| API 文档 | 开发者 | OpenAPI 自动生成 + 接入示例 |
| 用户手册 | 终端用户 | 功能说明、使用技巧、权限说明 |
| 运维手册 | SRE | 监控指标、告警处理、故障排查、扩容指南 |
| 数据管理员手册 | 知识管理员 | 文档导入流程、版本管理、权限配置 |

### 8.3 故障演练

| 故障场景 | 预期行为 |
|----------|----------|
| Redis 宕机 | L1 内存缓存接管, 无 503 |
| Milvus 超时 | ES Fallback 路径 |
| GPU0 OOM | 14B → 4B 自动降级 |
| vLLM-Rewrite 不可用 | 规则兜底路径 |
| ES 全部不可用 | 纯向量检索降级 |
| KV Pressure 0.95 | 分级降级策略 |

### 8.4 上线 Checklist

- [ ] 压测通过 (双卡 QPS ≥ 12, P95 ≤ 2s)
- [ ] 所有降级路径验证通过
- [ ] 故障演练完成, 无 P0 遗留
- [ ] 安全审计通过 (JWT/审计/脱敏/HTTPS)
- [ ] 数据导入完成, 知识库覆盖核心场景
- [ ] 监控面板就绪, 告警通知对接
- [ ] 回滚方案文档化, 可在 5 分钟内回滚
- [ ] 操作手册就绪, 运维人员培训完成
- [ ] 用户验收测试通过 (至少 5 名目标用户)
- [ ] 备份策略就绪 (Milvus/Redis/MinIO 定时备份)

---

## 9. 关键文件变更预估

### 新增文件

| 文件 | 说明 |
|------|------|
| `data/mock_generator.py` | Mock 数据生成器 |
| `data/mock_data/` | 生成的测试数据目录 |
| `scripts/start.sh` | 一键启动脚本 |
| `scripts/stop.sh` | 一键停止脚本 |
| `scripts/download_models.sh` | 模型下载脚本 |
| `docker-compose.gpu.yml` | GPU 服务覆盖层 |
| `docker-compose.cpu.yml` | CPU 降级覆盖层 |
| `tests/integration/` | 集成测试套件 |
| `tests/load/locustfile.py` | Locust 压测脚本 |
| `frontend/` | React Web UI |
| `nginx/nginx.conf` | Nginx 反向代理配置 |
| `deploy/` | 部署相关配置和脚本 |

### 修改文件

| 文件 | 变更 |
|------|------|
| `common/config.py` | 增加 deployment_mode, 模型路径环境变量化 |
| `app.py` | JWT 认证中间件, 审计日志中间件 |
| `models/llm_client.py` | 单卡复用逻辑 |
| `retrieval/rerank_batch_aggregator.py` | CPU ONNX fallback |
| `run_services.py` | 单卡服务启动逻辑 |
| `docker-compose.yml` | 基础设施与 GPU 服务分离 |
| `config.json` | deployment_mode 配置节 |
| `.env.example` | 新增环境变量 |

### 删除文件

| 文件 | 原因 |
|------|------|
| `agent_module/` | 汽车领域遗留代码 |
| `pipeline/__init__.py` | 空占位包 |
| `generation/__init__.py` | 空占位包 |
| `data/data.pdf` | 替换为 Mock 数据 |
| `data/image1.jpg` | 替换为 Mock 数据 |
| `data/text_mapping.json` | 替换为 Mock 数据 |
