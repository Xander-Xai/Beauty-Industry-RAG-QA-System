"""
检索与排序模块（readme 7 节）

模块组成：
- parallel_recall: 并行多路召回管理器（4路并行）
- dense_retriever: Dense 语义检索（BGE → Qdrant）
- bm25_retriever: BM25 关键词检索（ES）
- clip_retriever: CLIP 视觉语义检索（Qdrant image_512）
- bi_encoder: BiEncoder 宽保留重排（Stage 1）
- cross_encoder_ensemble: CrossEncoder Ensemble 重排（Stage 2）
- evidence_gate: Evidence Ensemble Gate（投票机制）
- answer_gate: Answer Gate（NLI 校验）
- rerank_batch_aggregator: GPU 微批聚合器
"""
