"""
FastAPI 服务层

API 拆分（对应 readme 13 节）：
- POST /query   - 单轮查询（完整 RAG 流程）
- POST /chat    - 多轮对话（带会话管理）
- GET  /health  - 健康检查（Redis/Milvus/ES 连通性）
- GET  /stats   - 系统指标（MetricsCollector）
"""
