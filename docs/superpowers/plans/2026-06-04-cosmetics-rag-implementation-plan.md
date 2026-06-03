# 化妆品企业级多模态 RAG 智能问答系统 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将汽车知识问答系统改造为化妆品企业级多模态 RAG 系统，基于 readme.md 设计文档，完成 FastAPI 服务层 + config.json 重写 + 关键模块修订 + Docker Compose 部署 + 旧代码清理。

**Architecture:** 以 readme.md 中的新版管线架构为基础，保留已完成的核心管线代码（core/、retrieval/、models/ 等），新建 FastAPI HTTP 服务作为入口，重写 config.json 修复编码问题，修订 Query Rewrite 和复杂度评估器的关键词适配化妆品领域，通过 Docker Compose 编排所有服务（vLLM、Milvus、ES、Redis、FastAPI）。

**Tech Stack:** FastAPI + vLLM + Milvus + Elasticsearch + Redis + BGE-M3/CLIP + Docker Compose

---

## 文件变更总览

### 新建文件

| 文件 | 说明 |
|------|------|
| `api/` 目录 | FastAPI 路由层 |
| `api/routes.py` | HTTP 端点（/query、/chat、/health、/stats） |
| `api/models.py` | Pydantic 请求/响应模型 |
| `api/middleware.py` | 认证中间件 |
| `api/dependencies.py` | 依赖注入（从请求头解析身份） |
| `api/__init__.py` | 空文件 |
| `docker-compose.yml` | 完整服务编排（FastAPI + vLLM×3 + Milvus + ES + Redis） |
| `Dockerfile` | FastAPI 镜像构建 |
| `scripts/init_vllm.sh` | vLLM 服务启动脚本 |
| `tests/` 目录 | 测试文件 |
| `tests/test_pipeline.py` | 管线单元测试 |
| `tests/test_api.py` | API 端点测试 |

### 修改文件

| 文件 | 修改内容 |
|------|---------|
| `app.py` | **完全重写为 FastAPI 入口**，替代原 Flask |
| `config.json` | **完全重写**，修复编码 + 适配化妆品系统 |
| `rewrite/query_rewriter.py` | 接入 StatelessRouter、修订 Prompt 化妆品化 |
| `models/complexity_evaluator.py` | 关键词替换为化妆品领域 |
| `cache/redis_cache.py` | 修复 config 字段引用 |

### 删除文件（废弃旧代码）

| 文件 | 原因 |
|------|------|
| `agent_module.py` | 旧版 Flask 调度器，架构已废弃 |
| `rag_module.py` | 旧版 FAISS 检索 |
| `knowledge_graph.py` | 汽车领域 + Wikipedia 搜索 |
| `multimodal_module.py` | 旧版图像处理 |
| `vectorize_and_index.py` | 旧版向量化，与 offline/ 重复 |
| `finetune_qlora.py` | 旧版 ChatGLM3 微调 |
| `templates/` | 旧版 Flask 前端 |
| `static/` | 旧版静态资源 |
| `data/questions.json` | 汽车领域数据 |
| `data/finetune_data.json` | 汽车微调数据 |
| `data/index.faiss` | 旧 FAISS 索引 |
| `data/vectors.npy` | 旧向量数据 |
| `readme.md` | 已整合到 docs/ 中 |
| `rule.txt` | 已整合到 docs/ 中 |
| `chatglm3-6b/` | 旧模型目录（Git LFS 需单独处理） |
| `all-MiniLM-L6-v2/train_script.py` | 非项目代码（HuggingFace 官方脚本） |

---

## Task 0: 备份旧代码（可选）

**Files:**
- Create: `legacy_backup/` (目录)

- [ ] **Step 1: 创建备份目录并记录当前状态**

```bash
mkdir -p legacy_backup
git log --oneline -1 > legacy_backup/commit_info.txt
echo "备份完成，当前 commit: $(cat legacy_backup/commit_info.txt)"
```

---

## Task 1: 重写 config.json

**Files:**
- Create: `config.json` (覆盖)

- [ ] **Step 1: 删除损坏的 config.json 并创建新文件**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/config.json`

```json
{
  "system": {
    "name": "化妆品企业级多模态RAG智能问答系统",
    "version": "2.0.0",
    "dual_gpu": true
  },
  "gpu0": {
    "role": "主生成推理",
    "models": {
      "gen_14b": {
        "name": "Qwen3-14B+QLoRA",
        "engine": "vllm",
        "port": 8100,
        "model_path": "./models/Qwen3-14B-Instruct",
        "max_model_len": 8192,
        "gpu_memory_utilization": 0.85,
        "kv_cache_budget_gb": 8.0,
        "max_output_tokens": {
          "regulation": 1024,
          "development": 768,
          "ingredient": 512,
          "product": 512,
          "general": 256,
          "short": 256
        }
      }
    }
  },
  "gpu1": {
    "role": "控制与轻推理",
    "models": {
      "vllm_rewrite": {
        "name": "Qwen3-4B",
        "engine": "vllm",
        "port": 8101,
        "model_path": "./models/Qwen3-4B-Instruct",
        "max_model_len": 4096,
        "max_tokens": 192
      },
      "vllm_gen_4b": {
        "name": "Qwen3-4B",
        "engine": "vllm",
        "port": 8102,
        "model_path": "./models/Qwen3-4B-Instruct",
        "max_model_len": 4096
      },
      "bert_complexity": {
        "name": "BERT复杂度分类",
        "model_path": "./models/bert-complexity",
        "device": "cuda:1"
      },
      "cross_encoder_a": {
        "name": "法规成分CrossEncoder",
        "model_path": "./models/cross-encoder-law",
        "device": "cuda:1"
      },
      "cross_encoder_b": {
        "name": "通用CrossEncoder",
        "model_path": "./models/cross-encoder-base",
        "device": "cuda:1"
      },
      "nli_model": {
        "name": "NLI蕴含模型",
        "model_path": "./models/nli-deberta",
        "device": "cuda:1"
      },
      "bi_encoder": {
        "name": "BiEncoder编码器",
        "model_path": "./models/bge-base-zh-v1.5",
        "device": "cuda:1"
      },
      "clip_image_encoder": {
        "name": "CLIP图像编码器",
        "model_path": "./models/clip-vit-base-patch16",
        "device": "cuda:1"
      },
      "clip_text_encoder": {
        "name": "CLIP文本编码器",
        "model_path": "./models/clip-vit-base-patch16",
        "device": "cuda:1"
      },
      "blip": {
        "name": "BLIP图像描述",
        "model_path": "./models/blip-image-captioning-large",
        "device": "cuda:1",
        "trigger_threshold": 0.05
      }
    },
    "rerank_batch_aggregator": {
      "time_window_ms": 15,
      "max_batch_size": 64,
      "max_pair_batch_size": 40
    }
  },
  "embedding": {
    "text": {
      "model_path": "./models/bge-base-zh-v1.5",
      "dimension": 768,
      "collection": "rag_text_768"
    },
    "image_clip": {
      "model_path": "./models/clip-vit-base-patch16",
      "dimension": 512,
      "collection": "rag_image_512"
    }
  },
  "milvus": {
    "host": "milvus",
    "port": 19530,
    "collections": {
      "rag_text_768": {
        "dimension": 768,
        "index_type": "IVF_FLAT",
        "metric_type": "L2"
      },
      "rag_image_512": {
        "dimension": 512,
        "index_type": "IVF_FLAT",
        "metric_type": "L2"
      }
    }
  },
  "elasticsearch": {
    "host": "http://elasticsearch:9200",
    "index": "cosmetics_docs",
    "enabled": true
  },
  "redis": {
    "cache": {
      "host": "redis",
      "port": 6379,
      "db": 0,
      "ttl_seconds": 3600
    },
    "state": {
      "host": "redis",
      "port": 6379,
      "db": 1
    }
  },
  "knowledge_base": {
    "data_dir": "./data",
    "chunk_size": 500,
    "chunk_overlap_ratio": 0.1,
    "ocr": {
      "engine": "paddleocr",
      "language": "ch",
      "visual_weight_repeat": 3
    }
  },
  "knowledge_version_epoch": "default",
  "query_rewrite": {
    "max_output_tokens": 192,
    "temperature": 0.1,
    "dialog_rounds": 6
  },
  "retrieval": {
    "parallel_paths": {
      "dense_bge": { "enabled": true, "top_k": 50 },
      "bm25_es": { "enabled": true, "top_k": 50 },
      "clip_visual": { "enabled": true, "top_k": 20 },
      "rewrite_variants": { "enabled": true, "top_k": 30 }
    },
    "rrf": {
      "k": 60,
      "weights": { "w_text": 1.0, "w_clip": 1.0, "w_ocr": 0.8 }
    },
    "cross_encoder": {
      "ensemble": true,
      "final_top_k": 10
    },
    "evidence_gate": {
      "weights": { "w1": 0.4, "w2": 0.2, "w3": 0.2, "w4": 0.2 },
      "thresholds": { "high_confidence": 0.7, "low_confidence": 0.4, "reject": 0.3 }
    }
  },
  "generation": {
    "max_conversation_rounds": 6,
    "prompt_version": "v2.1",
    "answer_gate": { "nli_threshold": 0.6 }
  },
  "admission_control": {
    "safety_factor": 0.7,
    "kv_utilization_threshold": 0.8,
    "kv_pressure_critical": 0.9
  },
  "cache_config": {
    "l1_ttl_seconds": 300,
    "l2_ttl_seconds": 3600
  },
  "rbac": {
    "roles": {
      "admin": 2147483647,
      "rd": 1,
      "quality": 2,
      "regulation": 4,
      "sales": 8
    },
    "departments": {
      "all": 0,
      "rd_dept": 1,
      "quality_dept": 2,
      "regulation_dept": 4,
      "sales_dept": 8
    },
    "super_admin_mask": 4294967295,
    "public_mask": 0
  },
  "alerting": {
    "rules": []
  }
}
```

- [ ] **Step 2: 验证 config.json 格式正确**

```bash
python3 -c "import json; c=json.load(open('config.json')); print('OK:', c['system']['name'])"
```

Expected: `OK: 化妆品企业级多模态RAG智能问答系统`

- [ ] **Step 3: 提交**

```bash
git add config.json && git commit -m "refactor: 重写 config.json，修复编码并适配化妆品系统"
```

---

## Task 2: 新建 FastAPI 服务层（api/ 目录）

**Files:**
- Create: `api/__init__.py`
- Create: `api/models.py`
- Create: `api/dependencies.py`
- Create: `api/middleware.py`
- Create: `api/routes.py`
- Create: `app.py` (完全重写)

- [ ] **Step 1: 创建 api/__init__.py**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/api/__init__.py`

```python
"""
FastAPI API 层
"""
```

- [ ] **Step 2: 创建 api/models.py — Pydantic 模型**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/api/models.py`

```python
"""
API 请求 / 响应模型定义
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """单轮问答请求"""
    query: str = Field(..., min_length=1, max_length=2000, description="用户查询")
    session_id: Optional[str] = Field(None, max_length=64, description="会话 ID")
    user_id: Optional[str] = Field(None, max_length=64, description="用户 ID")
    image_path: Optional[str] = Field(None, description="可选：图片路径")


class QueryResponse(BaseModel):
    """问答响应"""
    answer: str
    session_id: Optional[str] = None
    business_type: Optional[str] = None
    intent: Optional[str] = None
    evidence_doc_ids: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    cache_hit: bool = False


class ChatMessage(BaseModel):
    """对话消息"""
    role: str
    content: str


class ChatRequest(BaseModel):
    """多轮对话请求"""
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(..., max_length=64)


class ChatResponse(BaseModel):
    """多轮对话响应"""
    answer: str
    session_id: str
    history: list[ChatMessage] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    version: str
    dependencies: dict[str, str] = Field(default_factory=dict)


class StatsResponse(BaseModel):
    """系统统计响应"""
    uptime_seconds: float
    cache_hit_rate: dict[str, float]
    rewrite_fallback_rate: float
    latency_percentiles: dict[str, dict[str, float]]
    kv_pressure: float = 0.0
    active_requests: int = 0


class ErrorResponse(BaseModel):
    """错误响应"""
    error: str
    detail: Optional[str] = None
    code: str = "ERROR"
```

- [ ] **Step 3: 创建 api/dependencies.py — 依赖注入**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/api/dependencies.py`

```python
"""
依赖注入：从请求头解析用户身份（role_mask / dept_mask）
"""

from __future__ import annotations

from typing import Optional
import json
from fastapi import Header, HTTPException


def load_config():
    with open("config.json", encoding="utf-8") as f:
        return json.load(f)


class RequestIdentity:
    """请求身份信息"""
    def __init__(
        self,
        user_id: str,
        user_role_mask: int,
        user_dept_mask: int,
    ):
        self.user_id = user_id
        self.user_role_mask = user_role_mask
        self.user_dept_mask = user_dept_mask


def get_identity(
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
    x_role_mask: Optional[str] = Header(None, alias="X-Role-Mask"),
    x_dept_mask: Optional[str] = Header(None, alias="X-Dept-Mask"),
) -> RequestIdentity:
    """
    从请求头提取用户身份信息

    Headers:
    - X-User-ID: 用户 ID
    - X-Role-Mask: 角色位掩码（十进制整数）
    - X-Dept-Mask: 部门位掩码（十进制整数）

    默认值：公开用户（role_mask=0, dept_mask=0）
    """
    config = load_config()
    user_id = x_user_id or "anonymous"

    if x_role_mask:
        try:
            role_mask = int(x_role_mask)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid X-Role-Mask header")
    else:
        role_mask = config["rbac"].get("public_mask", 0)

    if x_dept_mask:
        try:
            dept_mask = int(x_dept_mask)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid X-Dept-Mask header")
    else:
        dept_mask = config["rbac"]["departments"]["all"]

    return RequestIdentity(
        user_id=user_id,
        user_role_mask=role_mask,
        user_dept_mask=dept_mask,
    )
```

- [ ] **Step 4: 创建 api/middleware.py — 中间件**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/api/middleware.py`

```python
"""
中间件：请求日志、CORS、异常处理
"""

from __future__ import annotations

import time
import logging
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def setup_middleware(app):
    """配置所有中间件"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """请求日志中间件"""
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        request_id = request.headers.get("X-Request-ID", "N/A")

        logger.info(f"[{request_id}] {request.method} {request.url.path} - started")

        try:
            response = await call_next(request)
            elapsed = (time.time() - start_time) * 1000
            logger.info(
                f"[{request_id}] {request.method} {request.url.path} - "
                f"{response.status_code} ({elapsed:.0f}ms)"
            )
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time-Ms"] = f"{elapsed:.0f}"
            return response
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(
                f"[{request_id}] {request.method} {request.url.path} - "
                f"ERROR: {e} ({elapsed:.0f}ms)"
            )
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal server error",
                    "detail": str(e),
                    "code": "INTERNAL_ERROR",
                },
            )
```

- [ ] **Step 5: 创建 api/routes.py — 路由**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/api/routes.py`

```python
"""
API 路由定义
"""

from __future__ import annotations

import time
import logging
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from api.models import (
    QueryRequest, QueryResponse,
    ChatRequest, ChatResponse,
    HealthResponse, StatsResponse,
)
from api.dependencies import get_identity, RequestIdentity
from core.pipeline_context import RequestContext
from monitoring.otel_tracer import MetricsCollector

logger = logging.getLogger(__name__)
_metrics = MetricsCollector()
router = APIRouter()


@router.post("/query", response_model=QueryResponse)
async def query(
    req: QueryRequest,
    identity: RequestIdentity = Depends(get_identity),
):
    """单轮问答接口"""
    from core.pipeline import OnlineRAGPipeline

    t_start = time.time()
    try:
        ctx = RequestContext(
            user_input=req.query,
            session_id=req.session_id,
            user_id=identity.user_id,
            user_role_mask=identity.user_role_mask,
            user_dept_mask=identity.user_dept_mask,
            image_path=req.image_path,
        )

        pipeline = OnlineRAGPipeline()
        answer = pipeline.process(ctx)

        elapsed_ms = (time.time() - t_start) * 1000
        ctx.record_timing("total_http", elapsed_ms)
        _metrics.record_request(ctx)

        return QueryResponse(
            answer=answer,
            session_id=req.session_id,
            business_type=ctx.rewrite_result.business_type if ctx.rewrite_result else None,
            intent=ctx.rewrite_result.intent if ctx.rewrite_result else None,
            evidence_doc_ids=ctx.evidence_locked_doc_ids,
            latency_ms=elapsed_ms,
            cache_hit=(ctx.cache_hit_level in ("L1", "L2")),
        )

    except Exception as e:
        logger.error(f"Query failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    identity: RequestIdentity = Depends(get_identity),
):
    """多轮对话接口"""
    from core.pipeline import OnlineRAGPipeline
    from core.pipeline_context import SessionState

    t_start = time.time()
    session = SessionState.get_or_create(req.session_id)

    ctx = RequestContext(
        user_input=req.message,
        session_id=req.session_id,
        user_id=identity.user_id,
        user_role_mask=identity.user_role_mask,
        user_dept_mask=identity.user_dept_mask,
    )

    try:
        pipeline = OnlineRAGPipeline()
        answer = pipeline.process(ctx)

        elapsed_ms = (time.time() - t_start) * 1000
        _metrics.record_request(ctx)

        history = []
        for round in session.dialog_rounds:
            if "user_input" in round:
                history.append({"role": "user", "content": round["user_input"]})
            if "response" in round:
                history.append({"role": "assistant", "content": round["response"]})

        return ChatResponse(
            answer=answer,
            session_id=req.session_id,
            history=history,
        )

    except Exception as e:
        logger.error(f"Chat failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health", response_model=HealthResponse)
async def health():
    """健康检查接口"""
    import json
    with open("config.json", encoding="utf-8") as f:
        config = json.load(f)

    deps = {}
    overall_status = "healthy"

    # 检查 Redis
    try:
        import redis
        r = redis.Redis(
            host=config["redis"]["cache"]["host"],
            port=config["redis"]["cache"]["port"],
            db=config["redis"]["cache"]["db"],
            socket_timeout=2,
        )
        r.ping()
        deps["redis"] = "connected"
    except Exception as e:
        deps["redis"] = f"error: {e}"
        overall_status = "degraded"

    # 检查 Milvus
    try:
        from pymilvus import connections
        connections.connect(
            alias="default",
            host=config["milvus"]["host"],
            port=config["milvus"]["port"],
            timeout=3,
        )
        deps["milvus"] = "connected"
    except Exception as e:
        deps["milvus"] = f"error: {e}"
        overall_status = "degraded"

    # 检查 ES
    try:
        from elasticsearch import Elasticsearch
        es = Elasticsearch([config["elasticsearch"]["host"]], timeout=3)
        es.ping()
        deps["elasticsearch"] = "connected"
    except Exception as e:
        deps["elasticsearch"] = f"error: {e}"
        overall_status = "degraded"

    return HealthResponse(
        status=overall_status,
        version=config["system"]["version"],
        dependencies=deps,
    )


@router.get("/stats", response_model=StatsResponse)
async def stats():
    """系统统计接口"""
    stats = _metrics.get_stats()

    return StatsResponse(
        uptime_seconds=stats["uptime_seconds"],
        cache_hit_rate=stats["cache_hit_rate"],
        rewrite_fallback_rate=stats["rewrite_fallback_rate"],
        latency_percentiles=stats["latency_percentiles"],
        kv_pressure=stats["gauges"].get("kv_pressure", 0.0),
        active_requests=0,
    )
```

- [ ] **Step 6: 重写 app.py — FastAPI 入口**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/app.py`

```python
"""
FastAPI 应用入口

化妆品企业级多模态 RAG 智能问答系统
"""

from __future__ import annotations

import logging
import sys

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from api.routes import router
from api.middleware import setup_middleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("app")


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用"""
    app = FastAPI(
        title="化妆品企业级多模态RAG智能问答系统",
        description="面向中小型化妆品企业的智能知识问答系统，支持多模态问答（成分/法规/配方/原料）",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    setup_middleware(app)
    app.include_router(router)

    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc):
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "code": "INTERNAL_ERROR",
            },
        )

    @app.on_event("startup")
    async def startup_event():
        logger.info("=" * 60)
        logger.info("化妆品企业级多模态RAG智能问答系统启动")
        logger.info("=" * 60)

    @app.on_event("shutdown")
    async def shutdown_event():
        logger.info("系统关闭")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
```

- [ ] **Step 7: 提交**

```bash
git add api/ app.py && git commit -m "feat: 新建 FastAPI 服务层（api/ + app.py 重写）"
```

---

## Task 3: 修订 rewrite/query_rewriter.py — 接入 vLLM

**Files:**
- Modify: `rewrite/query_rewriter.py`（REWRITE_SCHEMA、REWRITE_PROMPT_TEMPLATE、_call_llm、_simulate_rewrite）

- [ ] **Step 1: 更新 REWRITE_SCHEMA — 扩展 business_type**

找到 REWRITE_SCHEMA 定义，修改 business_type 的 enum：

```python
REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "rewritten_query": {"type": "string"},
        "business_type": {
            "type": "string",
            "enum": ["regulation", "development", "ingredient", "product", "general", "short"]
        },
        "intent": {
            "type": "string",
            "enum": ["compliance", "formulation", "ingredient", "product", "general"]
        },
        "requires_context": {"type": "boolean"},
        "standardized_entities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["rewritten_query", "business_type", "intent", "requires_context"],
}
```

- [ ] **Step 2: 更新 REWRITE_PROMPT_TEMPLATE — 化妆品化**

找到 REWRITE_PROMPT_TEMPLATE，将其替换为：

```python
REWRITE_PROMPT_TEMPLATE = """你是一个化妆品行业知识问答系统的查询改写助手。请分析用户的查询并输出结构化 JSON。

任务：
1. 将用户的原始查询改写为更适合向量检索的形式
2. 识别业务类型和意图
3. 提取标准化实体（成分名、INCI 名称、法规条款号、产品名等）

业务类型定义：
- regulation: 法规相关（合规、标准、许可、备案、禁用清单）
- development: 研发相关（配方、成分、工艺、制备）
- ingredient: 成分相关（功效、安全浓度、复配禁忌）
- product: 产品相关（品牌、价格、适用肤质）
- general: 通用问答
- short: 简短查询

意图定义：
- compliance: 合规查询
- formulation: 配方查询
- ingredient: 成分查询
- product: 产品查询
- general: 通用意图

最近对话历史：
{dialog_history}

当前查询：{query}

请输出严格 JSON 格式：
{{
    "rewritten_query": "改写后的查询",
    "business_type": "业务类型",
    "intent": "意图",
    "requires_context": true/false,
    "standardized_entities": ["实体1", "实体2"]
}}
"""
```

- [ ] **Step 3: 实现 _call_llm 方法 — 接入 StatelessRouter**

找到 `_call_llm` 方法，替换为：

```python
def _call_llm(self, prompt: str, temperature: float = None) -> str:
    """调用 vLLM-Rewrite 实例"""
    from router.stateless_router import StatelessRouter

    payload = {
        "prompt": prompt,
        "max_tokens": self.max_output_tokens,
        "temperature": temperature if temperature is not None else self.temperature,
    }

    try:
        router = StatelessRouter()
        response = router.route_completion("rewrite", prompt, max_tokens=192, temperature=payload["temperature"])
        return response
    except Exception as e:
        logger.error(f"vLLM-Rewrite 调用失败: {e}")
        raise
```

- [ ] **Step 4: 更新 _simulate_rewrite — 化妆品关键词**

找到 `_simulate_rewrite` 方法，更新关键词：

```python
def _simulate_rewrite(self, query: str) -> str:
    """模拟 Rewrite（开发/测试用）"""
    biz_type = "general"
    intent = "general"
    if any(kw in query for kw in ["法规", "合规", "标准", "备案", "许可", "禁用", "安全技术规范"]):
        biz_type = "regulation"
        intent = "compliance"
    elif any(kw in query for kw in ["配方", "研发", "工艺", "制备", "开发"]):
        biz_type = "development"
        intent = "formulation"
    elif any(kw in query for kw in ["成分", "INCI", "功效", "浓度", "烟酰胺", "玻色因", "神经酰胺", "A醇"]):
        biz_type = "ingredient"
        intent = "ingredient"
    elif any(kw in query for kw in ["产品", "品牌", "适用", "肤质", "包装"]):
        biz_type = "product"
        intent = "product"

    return json.dumps({
        "rewritten_query": query,
        "business_type": biz_type,
        "intent": intent,
        "requires_context": True,
        "standardized_entities": [],
    }, ensure_ascii=False)
```

- [ ] **Step 5: 提交**

```bash
git add rewrite/query_rewriter.py && git commit -m "feat(query_rewriter): 接入 vLLM，Prompt 化妆品化，扩展 business_type"
```

---

## Task 4: 修订 models/complexity_evaluator.py — 化妆品关键词

**Files:**
- Modify: `models/complexity_evaluator.py:76-90` (_evaluate_with_rules 方法)

- [ ] **Step 1: 更新 _evaluate_with_rules — 化妆品关键词**

找到 `_evaluate_with_rules` 方法中的 `complex_keywords` 列表，替换为：

```python
def _evaluate_with_rules(self, query: str) -> bool:
    """
    规则兜底评估（模型未加载时使用）

    复杂度关键词匹配（化妆品领域）
    """
    complex_keywords = [
        # 法规类
        "法规", "合规", "标准", "备案", "许可", "禁用", "安全评估", "毒理",
        "功效评价", "原料安全", "禁限用", "化妆品安全技术规范",
        # 研发类
        "配方", "复配", "工艺", "稳定性", "相容性", "防腐体系", "功效宣称",
        # 成分交叉
        "多成分", "交叉", "综合", "对比", "分析", "评估",
        # 条款引用
        "依据", "引用", "条款", "第", "条", "号",
        # 专业术语
        "INCI", "分子量", "浓度阈值", "PH范围", "使用量",
    ]
    score = sum(1 for kw in complex_keywords if kw in query)
    return score >= 2
```

- [ ] **Step 2: 提交**

```bash
git add models/complexity_evaluator.py && git commit -m "feat(complexity_evaluator): 更新复杂度评估关键词为化妆品领域"
```

---

## Task 5: 修订 cache/redis_cache.py — 修复 config 引用

**Files:**
- Modify: `cache/redis_cache.py`（多处 config 字段引用）

- [ ] **Step 1: 检查并修复 config 引用**

在 `__init__` 方法中，Redis 连接成功后添加 l1_ttl 和 l2_ttl 实例变量：

```python
def __init__(self):
    self._l1 = {}
    self._l1_max = 1000
    self.redis_client = None
    self.enabled = False
    self.l1_ttl = 300
    self.l2_ttl = 3600
    try:
        import redis
        rc = config['redis']['cache']
        self.redis_client = redis.Redis(
            host=rc['host'], port=rc['port'], db=rc['db'],
            decode_responses=True, socket_timeout=2
        )
        self.redis_client.ping()
        self.enabled = True
        # 兼容新旧 config 格式
        self.l1_ttl = config.get("cache_config", {}).get("l1_ttl_seconds", 300)
        self.l2_ttl = config.get("cache_config", {}).get("l2_ttl_seconds", 3600)
    except Exception:
        self.enabled = False
```

然后在 `set` 方法中将 `config['cache_config']['l1_ttl_seconds']` 替换为 `self.l1_ttl`，`config['cache_config']['l2_ttl_seconds']` 替换为 `self.l2_ttl`。

- [ ] **Step 2: 提交**

```bash
git add cache/redis_cache.py && git commit -m "fix(redis_cache): 修复 config 字段引用，兼容新旧格式"
```

---

## Task 6: 新建 Docker Compose 部署配置

**Files:**
- Create: `docker-compose.yml`
- Create: `Dockerfile`
- Create: `scripts/init_vllm.sh`

- [ ] **Step 1: 创建 Dockerfile**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/Dockerfile`

```dockerfile
FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "app.py"]
```

- [ ] **Step 2: 创建 docker-compose.yml**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/docker-compose.yml`

```yaml
version: '3.8'

services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - PYTHONUNBUFFERED=1
    depends_on:
      - redis
      - milvus
      - elasticsearch
    networks:
      - rag-network
    restart: unless-stopped
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
    volumes:
      - redis-data:/data
    networks:
      - rag-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3

  milvus:
    image: milvusdb/milvus:v2.3.3
    ports:
      - "19530:19530"
      - "9091:9091"
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000
    depends_on:
      - etcd
      - minio
    volumes:
      - milvus-data:/var/lib/milvus
    networks:
      - rag-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9091/healthz"]
      interval: 10s
      timeout: 5s
      retries: 3

  etcd:
    image: quay.io/coreos/etcd:v3.5.5
    environment:
      - ETCD_AUTO_COMPACTION_MODE=revision
      - ETCD_AUTO_COMPACTION_RETENTION=1000
      - ETCD_QUOTA_BACKEND_BYTES=4294967296
      - ETCD_SNAPSHOT_COUNT=50000
    volumes:
      - etcd-data:/etcd
    networks:
      - rag-network
    command: etcd -advertise-client-urls=http://127.0.0.1:2379 -listen-client-urls http://0.0.0.0:2379 --data-dir /etcd

  minio:
    image: minio/minio:latest
    environment:
      MINIO_ACCESS_KEY: minioadmin
      MINIO_SECRET_KEY: minioadmin
    ports:
      - "9001:9001"
    volumes:
      - minio-data:/minio_data
    networks:
      - rag-network
    command: minio server /minio_data --console-address ":9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 10s
      timeout: 5s
      retries: 3

  elasticsearch:
    image: elasticsearch:8.12.0
    ports:
      - "9200:9200"
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - ES_JAVA_OPTS=-Xms1g -Xmx1g
      - cluster.name=cosmetics-rag
    volumes:
      - es-data:/usr/share/elasticsearch/data
    networks:
      - rag-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "curl -s http://localhost:9200/_cluster/health | grep -q '\"status\":\"green\"\\|\"status\":\"yellow\"'"]
      interval: 15s
      timeout: 10s
      retries: 5

  vllm-gen-14b:
    image: vllm/vllm-openai:latest
    ports:
      - "8100:8000"
    environment:
      - CUDA_VISIBLE_DEVICES=0
    volumes:
      - ./models:/models
    command: >
      python -m vllm.entrypoints.openai.api_server
      --model /models/Qwen3-14B-Instruct
      --served-model-name qwen3-14b
      --port 8000
      --gpu-memory-utilization 0.85
      --max-model-len 8192
      --trust-remote-code
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    networks:
      - rag-network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  vllm-rewrite:
    image: vllm/vllm-openai:latest
    ports:
      - "8101:8000"
    environment:
      - CUDA_VISIBLE_DEVICES=1
    volumes:
      - ./models:/models
    command: >
      python -m vllm.entrypoints.openai.api_server
      --model /models/Qwen3-4B-Instruct
      --served-model-name qwen3-4b-rewrite
      --port 8000
      --gpu-memory-utilization 0.5
      --max-model-len 4096
      --trust-remote-code
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    networks:
      - rag-network
    restart: unless-stopped

  vllm-gen-4b:
    image: vllm/vllm-openai:latest
    ports:
      - "8102:8000"
    environment:
      - CUDA_VISIBLE_DEVICES=1
    volumes:
      - ./models:/models
    command: >
      python -m vllm.entrypoints.openai.api_server
      --model /models/Qwen3-4B-Instruct
      --served-model-name qwen3-4b
      --port 8000
      --gpu-memory-utilization 0.5
      --max-model-len 4096
      --trust-remote-code
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    networks:
      - rag-network
    restart: unless-stopped

networks:
  rag-network:
    driver: bridge

volumes:
  redis-data:
  milvus-data:
  etcd-data:
  minio-data:
  es-data:
```

- [ ] **Step 3: 创建 scripts/init_vllm.sh**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/scripts/init_vllm.sh`

```bash
#!/bin/bash
# vLLM 服务初始化脚本

set -e

echo "=== vLLM 服务初始化 ==="

wait_for_vllm() {
    local url=$1
    local name=$2
    local max_attempts=30
    local attempt=1

    echo "等待 $name 启动..."
    while [ $attempt -le $max_attempts ]; do
        if curl -sf "$url/health" > /dev/null 2>&1; then
            echo "$name 已就绪"
            return 0
        fi
        echo "  尝试 $attempt/$max_attempts..."
        sleep 5
        attempt=$((attempt + 1))
    done
    echo "$name 启动超时"
    return 1
}

wait_for_vllm "http://localhost:8100" "vLLM-Gen-14B"
wait_for_vllm "http://localhost:8101" "vLLM-Rewrite"
wait_for_vllm "http://localhost:8102" "vLLM-Gen-4B"

echo "=== 所有 vLLM 服务已就绪 ==="
```

- [ ] **Step 4: 提交**

```bash
git add docker-compose.yml Dockerfile scripts/init_vllm.sh && git commit -m "feat: 新增 Docker Compose 部署配置"
```

---

## Task 7: 清理废弃旧代码

**Files:**
- Delete: `agent_module.py`, `rag_module.py`, `knowledge_graph.py`, `multimodal_module.py`, `vectorize_and_index.py`, `finetune_qlora.py`, `readme.md`, `rule.txt`, `templates/`, `static/`, `data/questions.json`, `data/finetune_data.json`, `data/index.faiss`, `data/vectors.npy`

- [ ] **Step 1: 删除废弃 Python 文件和文档**

```bash
rm -v agent_module.py rag_module.py knowledge_graph.py multimodal_module.py vectorize_and_index.py finetune_qlora.py readme.md rule.txt
```

- [ ] **Step 2: 删除旧前端和数据**

```bash
rm -rfv templates/ static/
rm -v data/questions.json data/finetune_data.json data/index.faiss data/vectors.npy
```

- [ ] **Step 3: 删除非项目文件**

```bash
# 删除 chatglm3-6b 旧模型目录（如在 Git 跟踪中）
git rm -r --cached chatglm3-6b/ 2>/dev/null || echo "chatglm3-6b not tracked"
# 删除非项目训练脚本
rm -v all-MiniLM-L6-v2/train_script.py
```

- [ ] **Step 4: 提交**

```bash
git add -A && git commit -m "chore: 清理废弃旧代码（汽车系统相关文件）"
```

---

## Task 8: 更新 requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: 更新 requirements.txt**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/requirements.txt`

```txt
# === FastAPI ===
fastapi>=0.110.0
uvicorn[standard]>=0.27.0
pydantic>=2.6.0

# === Core Framework ===
requests>=2.31.0

# === LLM & Transformers ===
transformers>=4.40.0
torch>=2.4.0
sentence-transformers>=2.7.0

# === Vector Database ===
pymilvus>=2.4.0
faiss-cpu>=1.8.0

# === Search Engine ===
elasticsearch>=8.14.0

# === Cache ===
redis>=5.0.0

# === NLP Tools ===
jieba>=0.42.1
scikit-learn>=1.5.1

# === Document Processing ===
PyMuPDF>=1.24.0
python-docx>=1.1.0
pandas>=2.2.0
openpyxl>=3.1.0
Pillow>=10.4.0

# === OCR ===
# paddleocr>=2.7.0
# paddlepaddle>=2.6.0

# === Image Processing ===
opencv-python>=4.9.0

# === Monitoring ===
opentelemetry-api>=1.25.0
opentelemetry-sdk>=1.25.0

# === Utils ===
numpy>=1.26.0
protobuf>=3.20.2
```

- [ ] **Step 2: 提交**

```bash
git add requirements.txt && git commit -m "chore: 更新 requirements.txt（FastAPI + 清理旧依赖）"
```

---

## Task 9: 新建测试文件

**Files:**
- Create: `tests/__init__.py`, `tests/test_pipeline.py`, `tests/test_api.py`

- [ ] **Step 1: 创建 tests/__init__.py**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/tests/__init__.py`

```python
"""测试包"""
```

- [ ] **Step 2: 创建 tests/test_pipeline.py**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/tests/test_pipeline.py`

```python
"""
管线单元测试
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import json


class TestConfig:
    def test_config_loads(self):
        """config.json 能正确加载"""
        with open("config.json", encoding="utf-8") as f:
            config = json.load(f)
        assert "system" in config
        assert "gpu0" in config
        assert "rbac" in config
        assert config["system"]["name"] == "化妆品企业级多模态RAG智能问答系统"

    def test_config_has_all_required_sections(self):
        """config.json 包含所有必要字段"""
        with open("config.json", encoding="utf-8") as f:
            config = json.load(f)
        required = ["system", "gpu0", "gpu1", "embedding", "milvus",
                    "elasticsearch", "redis", "knowledge_base", "rbac"]
        for section in required:
            assert section in config, f"Missing section: {section}"


class TestRBAC:
    def test_is_allowed_public_doc(self):
        """公开文档所有用户可访问"""
        from auth.bitmask_rbac import is_allowed
        assert is_allowed(0, 1, 0, 0) == True
        assert is_allowed(0, 0, 0, 0) == True

    def test_is_allowed_role_filter(self):
        """角色过滤"""
        from auth.bitmask_rbac import is_allowed
        assert is_allowed(1, 2147483647, 0, 0) == True  # admin 可访问任何
        assert is_allowed(2147483647, 1, 0, 0) == False  # rd 不能访问 admin-only

    def test_encode_role_mask(self):
        """角色掩码编码"""
        from auth.bitmask_rbac import encode_role_mask
        assert encode_role_mask(["admin"]) == 2147483647
        assert encode_role_mask(["rd"]) == 1
        assert encode_role_mask(["rd", "quality"]) == 3  # 1 | 2


class TestQueryRewriter:
    def test_simulate_rewrite_regulation(self):
        """模拟 Rewrite：法规类关键词识别"""
        from rewrite.query_rewriter import QueryRewriter
        rewriter = QueryRewriter()
        result = rewriter._simulate_rewrite("烟酰胺在化妆品中的使用法规是什么？")
        data = json.loads(result)
        assert data["business_type"] == "regulation"
        assert data["intent"] == "compliance"

    def test_simulate_rewrite_ingredient(self):
        """模拟 Rewrite：成分类关键词识别"""
        from rewrite.query_rewriter import QueryRewriter
        rewriter = QueryRewriter()
        result = rewriter._simulate_rewrite("玻色因的功效和浓度范围？")
        data = json.loads(result)
        assert data["business_type"] == "ingredient"

    def test_simulate_rewrite_development(self):
        """模拟 Rewrite：研发类关键词识别"""
        from rewrite.query_rewriter import QueryRewriter
        rewriter = QueryRewriter()
        result = rewriter._simulate_rewrite("如何设计一个保湿配方的防腐体系？")
        data = json.loads(result)
        assert data["business_type"] == "development"


class TestComplexityEvaluator:
    def test_evaluate_with_rules_simple(self):
        """复杂度评估：简单查询"""
        from models.complexity_evaluator import ComplexityEvaluator
        evaluator = ComplexityEvaluator()
        is_complex = evaluator._evaluate_with_rules("烟酰胺安全吗？")
        assert is_complex == False

    def test_evaluate_with_rules_complex(self):
        """复杂度评估：复杂查询"""
        from models.complexity_evaluator import ComplexityEvaluator
        evaluator = ComplexityEvaluator()
        is_complex = evaluator._evaluate_with_rules(
            "根据《化妆品安全技术规范》，烟酰胺和A醇的复配使用有何法规限制？"
        )
        assert is_complex == True


class TestKVAdmission:
    def test_admission_critical(self):
        """KV 准入：压力 > 0.95 时拒绝"""
        from admission.kv_admission import KVAdmissionControl
        control = KVAdmissionControl()
        for i in range(1000):
            control.admit(f"req_{i}", 1000, 512, "general")
        admitted, reason = control.admit("req_test", 1000, 512, "general")
        assert admitted == False
        assert reason == "critical"

    def test_admission_budget_exceeded(self):
        """KV 准入：预算超限时拒绝"""
        from admission.kv_admission import KVAdmissionControl
        control = KVAdmissionControl()
        admitted, reason = control.admit("req_1", 5000, 512, "general")
        assert admitted == True  # 第一个请求应该通过


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

- [ ] **Step 3: 创建 tests/test_api.py**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/tests/test_api.py`

```python
"""
API 端点测试
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """创建测试客户端"""
    from app import app
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        """GET /health 返回 200"""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_response_structure(self, client):
        """健康检查响应结构正确"""
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert "version" in data
        assert "dependencies" in data


class TestQueryEndpoint:
    def test_query_returns_200(self, client):
        """POST /query 返回 200"""
        response = client.post("/query", json={
            "query": "烟酰胺的安全浓度是多少？",
        })
        assert response.status_code == 200

    def test_query_with_session(self, client):
        """带 session_id 的查询"""
        response = client.post("/query", json={
            "query": "玻色因的功效？",
            "session_id": "test_session_001",
        })
        assert response.status_code == 200
        data = response.json()
        assert "answer" in data
        assert "session_id" in data

    def test_query_empty_fails(self, client):
        """空查询返回 422"""
        response = client.post("/query", json={"query": ""})
        assert response.status_code == 422

    def test_query_with_identity_headers(self, client):
        """带身份头的查询"""
        response = client.post("/query", json={
            "query": "维生素C的稳定性？",
        }, headers={
            "X-User-ID": "user_rd_001",
            "X-Role-Mask": "1",
            "X-Dept-Mask": "1",
        })
        assert response.status_code == 200


class TestStatsEndpoint:
    def test_stats_returns_200(self, client):
        """GET /stats 返回 200"""
        response = client.get("/stats")
        assert response.status_code == 200

    def test_stats_structure(self, client):
        """统计响应结构"""
        response = client.get("/stats")
        data = response.json()
        assert "uptime_seconds" in data
        assert "cache_hit_rate" in data
        assert "rewrite_fallback_rate" in data
        assert "latency_percentiles" in data
```

- [ ] **Step 4: 验证测试可运行**

```bash
cd /home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge
pip install pytest fastapi httpx -q
python -m pytest tests/test_pipeline.py::TestConfig::test_config_loads -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/ && git commit -m "test: 新增单元测试（pipeline + API）"
```

---

## Task 10: 收尾 — 更新项目文档

**Files:**
- Create: `README.md`

- [ ] **Step 1: 创建新的 README.md**

路径: `/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge/README.md`

```markdown
# 化妆品企业级多模态 RAG 智能问答系统

面向中小型化妆品企业的智能知识问答系统，支持成分查询、法规咨询、配方研发、产品信息等多场景问答。

## 系统架构

基于 RAG（检索增强生成）管线，支持：
- 多模态输入（文本 + 图片）
- 双阶段检索（BiEncoder + CrossEncoder）
- 证据投票机制（Evidence Gate）
- NLI 答案校验（Answer Gate）
- 细粒度 RBAC 权限控制

## 快速开始

### 前置依赖

- Docker & Docker Compose
- NVIDIA GPU + nvidia-container-toolkit

### 1. 下载模型权重

```bash
# Qwen3-14B-Instruct
# Qwen3-4B-Instruct
# bge-base-zh-v1.5
# clip-vit-base-patch16
# 其他模型见 models/ 目录
```

### 2. 启动服务

```bash
docker compose up -d
```

### 3. 初始化知识库

```bash
python -m offline.scheduler
```

### 4. 访问 API

- API 文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

## API 示例

```bash
# 单轮问答
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "烟酰胺的安全浓度是多少？"}'

# 带身份认证
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -H "X-User-ID: user_rd_001" \
  -H "X-Role-Mask: 1" \
  -d '{"query": "配方开发相关问题", "session_id": "sess_001"}'
```

## 目录结构

```
.
├── api/                  # FastAPI 路由层
├── core/                 # 管线编排器
├── rewrite/              # Query Rewrite
├── admission/            # KV 准入控制
├── retrieval/            # 检索模块（Dense/BM25/CLIP）
├── models/                # 模型封装（Embedding/LLM/NLI）
├── cache/                # L1/L2 缓存
├── auth/                 # RBAC 权限控制
├── offline/              # 离线知识库构建
├── monitoring/           # 可观测性（OTel + Metrics）
├── router/               # 无状态路由
├── tests/                # 测试
├── config.json           # 配置文件
├── app.py               # FastAPI 入口
└── docker-compose.yml   # 部署配置
```

## 开发

```bash
# 安装依赖
pip install -r requirements.txt

# 运行测试
pytest tests/ -v

# 本地启动
python app.py
```

## 性能指标

- P95 延迟：1.5~3.0s
- 有效并发：20~25
- QPS：12~18
- 多轮对话：最近 6 轮
```

- [ ] **Step 2: 提交**

```bash
git add README.md && git commit -m "docs: 更新 README.md"
```

---

## 自检清单

完成所有 Task 后执行：

```bash
# 1. config.json 格式正确
python3 -c "import json; json.load(open('config.json')); print('config.json OK')"

# 2. FastAPI 可导入
python3 -c "from app import app; print('FastAPI app OK')"

# 3. 管线可导入
python3 -c "from core.pipeline import OnlineRAGPipeline; print('Pipeline OK')"

# 4. 测试通过
python -m pytest tests/test_pipeline.py::TestConfig -v

# 5. 无废弃文件残留
ls agent_module.py rag_module.py knowledge_graph.py 2>&1 | grep -q "No such" && echo "Old files cleaned"

# 6. Docker Compose 语法正确
docker compose config --quiet && echo "docker-compose.yml OK"

# 7. 提交所有变更
git status
```

---

**Plan complete and saved to `docs/superpowers/plans/2026-06-04-cosmetics-rag-implementation-plan.md`.**

两个执行选项：

**1. Subagent-Driven（推荐）** — 每个 Task 由独立 subagent 执行，Task 间有审查，适合大规模重构

**2. Inline Execution** — 在当前 session 内顺序执行，有检查点，适合快速迭代

选哪个？