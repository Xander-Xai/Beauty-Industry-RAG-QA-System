# 汽车知识智能问答系统 - 微服务化架构设计

> 日期: 2026-06-04
> 版本: v1.0.0

## 1. 概述

### 1.1 背景

原项目为化妆品行业多模态 RAG 系统，采用单体架构。现需进行架构重构，升级为微服务架构，以提高可维护性、可扩展性和故障隔离能力。

### 1.2 目标

- **代码结构优化**: 更好的模块边界、更清晰的依赖关系
- **技术栈升级**: Flask → FastAPI + 异步处理
- **架构规范化**: 标准化组件接口、统一异常处理

### 1.3 设计原则

1. 服务独立部署、独立扩缩容
2. 服务间通过 HTTP REST + Redis Stream 通信
3. 每个服务可独立测试和验证
4. 故障隔离，单服务故障不影响整体

---

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         API Gateway                              │
│                    (FastAPI, 端口 8000)                          │
│         统一入口 / 鉴权 / 限流 / 链路追踪                          │
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
    ┌──────────┐       ┌──────────┐       ┌──────────┐
    │ Rewrite   │       │ Retrieval│       │ Generation│
    │ Service   │       │ Service  │       │ Service   │
    │ (8101)    │       │ (8200)   │       │ (8100/8103)│
    └──────────┘       └──────────┘       └──────────┘
          │                   │                   │
          │                   │                   │
          ▼                   ▼                   ▼
    ┌──────────┐       ┌──────────┐       ┌──────────┐
    │  LLM     │       │  Milvus  │       │  Redis   │
    │ (vLLM)   │       │    ES    │       │  Stream  │
    └──────────┘       └──────────┘       └──────────┘
```

### 2.2 服务职责

| 服务 | 端口 | 职责 | 依赖 |
|------|------|------|------|
| `api-gateway` | 8000 | 统一入口、路由分发、鉴权 | 所有服务 |
| `rewrite-service` | 8101 | Query Rewrite、意图分类 | LLM (vLLM) |
| `retrieval-service` | 8200 | 并行多路召回、Rerank | Milvus, ES, Cache |
| `generation-service` | 8100/8103 | LLM 生成、Answer Gate | vLLM (14B/4B) |
| `cache-service` | 8300 | L1/L2 缓存管理 | Redis |
| `monitoring-service` | 8400 | 指标采集、告警 | - |

---

## 3. 服务详细设计

### 3.1 API Gateway

**职责**:
- 统一入口，所有客户端请求经由此
- JWT 鉴权、限流
- 请求路由分发
- 链路追踪 (OpenTelemetry)

**API 接口**:

```yaml
POST /v1/rewrite:
  route_to: rewrite-service
  timeout: 45ms

POST /v1/retrieve:
  route_to: retrieval-service
  timeout: 200ms

POST /v1/generate:
  route_to: generation-service
  timeout: 3000ms

GET /v1/health:
  route_to: all-services
```

### 3.2 Rewrite Service

**职责**:
- Query Rewrite（路由增强器）
- 意图分类（business_type, intent）
- 失败降级为规则兜底

**通信协议**: HTTP REST

### 3.3 Retrieval Service

**职责**:
- 并行多路召回（Dense/BM25/CLIP/Rewrite变体）
- BiEncoder 宽保留（Top150）
- CrossEncoder Ensemble 精排
- Evidence Ensemble Gate

### 3.4 Generation Service

**职责**:
- LLM 生成（4B/14B 路由）
- Answer Gate（NLI 校验）
- 长文本续写（上下文重建）

### 3.5 Cache Service

**职责**:
- L1 内存缓存（进程内 LRU）
- L2 Redis 缓存（权限绑定 Key）
- 版本化失效（knowledge_version_epoch）

### 3.6 Monitoring Service

**职责**:
- 指标采集（延迟、QPS、KV利用率）
- 告警管理
- 链路追踪数据聚合

---

## 4. 目录结构

```
automotive-qa-system/
├── api-gateway/
│   ├── main.py
│   ├── routers/
│   │   ├── rewrite.py
│   │   ├── retrieval.py
│   │   └── generation.py
│   ├── middleware/
│   │   ├── auth.py
│   │   ├── rate_limit.py
│   │   └── tracing.py
│   └── requirements.txt
│
├── rewrite-service/
│   ├── main.py
│   ├── router.py
│   ├── llm_client.py
│   └── requirements.txt
│
├── retrieval-service/
│   ├── main.py
│   ├── parallel_recall.py
│   ├── rerank/
│   │   ├── bi_encoder.py
│   │   ├── cross_encoder.py
│   │   └── evidence_gate.py
│   └── requirements.txt
│
├── generation-service/
│   ├── main.py
│   ├── llm_client.py
│   └── answer_gate.py
│
├── cache-service/
│   ├── main.py
│   ├── l1_cache.py
│   └── l2_cache.py
│
├── monitoring-service/
│   ├── main.py
│   ├── metrics.py
│   └── alerting.py
│
├── common/                   # 共享模块
│   ├── config.py
│   ├── models.py
│   ├── exceptions.py
│   └── tracing.py
│
├── docker-compose.yml
└── README.md
```

---

## 5. 服务间通信

### 5.1 HTTP REST (同步)

```python
# API Gateway → Rewrite Service
POST http://rewrite-service:8101/api/rewrite
{
    "query": "xxx",
    "recent_dialogs": [...]
}

# API Gateway → Retrieval Service
POST http://retrieval-service:8200/api/retrieve
{
    "query": "xxx",
    "user_role_mask": 0,
    "user_dept_mask": 0
}
```

### 5.2 Redis Stream (异步任务)

```python
# 任务队列
TaskQueue: rag:tasks:{service}
Results: rag:results:{request_id}

# 任务结构
{
    "request_id": "uuid",
    "service": "rewrite/retrieval/generation",
    "payload": {...},
    "timestamp": 1234567890
}
```

---

## 6. 实施计划

### Phase 1: 基础设施 (1-2 周)
1. 创建项目目录结构
2. 实现 `common` 共享模块
3. 配置 Docker Compose

### Phase 2: 核心服务 (2-3 周)
1. API Gateway
2. Cache Service
3. Rewrite Service

### Phase 3: 检索与生成 (2-3 周)
1. Retrieval Service
2. Generation Service

### Phase 4: 监控与集成 (1-2 周)
1. Monitoring Service
2. 端到端集成测试

---

## 7. 验证策略

每个模块实现后，通过以下方式验证：
1. 单元测试（pytest）
2. 服务独立启动测试
3. 服务间通信测试
4. 端到端集成测试

---

## 8. 参考

- 原 readme.md 设计规格
- 原 core/pipeline.py 实现逻辑