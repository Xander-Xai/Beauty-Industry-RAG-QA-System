"""Rerank Batch Aggregator — 转发到唯一实现。

本模块曾携带 ``retrieval/rerank_batch_aggregator.py`` 的第二份拷贝，且已经漂移：
服务拷贝只有同步 ``batch_predict``，缺少异步跨请求聚合（``submit_batch``）、
GPU 失败 CPU 回退（``batch_predict_with_fallback``）以及 P99 SLA 自适应窗口。
两份实现现在在 ``retrieval/rerank_batch_aggregator.py`` 合一，本模块只做转发以
保留 ``retrieval-service/main.py`` 使用的导入路径。
"""

from retrieval.rerank_batch_aggregator import (  # noqa: F401  (re-exported for the service entry point)
    BatchStats,
    RerankBatchAggregator,
)

__all__ = ["RerankBatchAggregator", "BatchStats"]
