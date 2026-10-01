"""
模型服务模块

模块组成：
- embedding_service: BGE/CLIP Embedding 编码 + Qdrant 检索
- complexity_evaluator: BERT 复杂度评估（简单/复杂路由）
- llm_client: LLM 客户端（vLLM 双实例调用 + PEFT Adapter 管理）
- adapter_manager: PEFT Adapter 生命周期管理器（发现/验证/加载/切换/卸载）
"""

from models.adapter_manager import AdapterManager

__all__ = ["AdapterManager"]
