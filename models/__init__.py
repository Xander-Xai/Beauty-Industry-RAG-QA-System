"""
模型服务模块

模块组成：
- embedding_service: BGE/CLIP Embedding 编码 + Milvus 检索
- complexity_evaluator: BERT 复杂度评估（简单/复杂路由）
- llm_client: LLM 客户端（vLLM 双实例调用）
"""
