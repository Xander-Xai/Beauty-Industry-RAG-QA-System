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
- 健康检查: http://localhost:8000/api/health

## API 示例

```bash
# 单轮问答
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query": "烟酰胺的安全浓度是多少？"}'

# 带身份认证
curl -X POST http://localhost:8000/api/query \
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
├── models/               # 模型封装（Embedding/LLM/NLI）
├── cache/                # L1/L2 缓存
├── auth/                 # RBAC 权限控制
├── offline/              # 离线知识库构建
├── monitoring/           # 可观测性（OTel + Metrics）
├── router/               # 无状态路由
├── tests/                # 测试
├── config.json           # 配置文件
├── app.py                # FastAPI 入口
└── docker-compose.yml    # 部署配置
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
