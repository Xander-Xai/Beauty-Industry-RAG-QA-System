"""BiEncoder 宽保留重排 — 转发到唯一实现。

本模块曾携带 ``retrieval/bi_encoder.py`` 的第二份拷贝。两份实现并不等价：
这份服务拷贝多出一个 ``dedicated_model_available()`` /
``_rerank_with_dedicated_model()`` 专用模型路径，而在线实现没有。为避免
“修一处、漏一处”导致在线管线与已部署的 ``retrieval-service`` 排序不一致，
专用模型路径已并入 ``retrieval/bi_encoder.py``，本模块只做转发。

``retrieval-service`` 是已部署的微服务（``docker-compose.microservices.yml``，
端口 8200，由 API 网关到达），而 ``retrieval-service/main.py`` 从这里导入，
所以保留该导入路径；两个部署现在共享同一实现与同一份排序逻辑。
"""

from retrieval.bi_encoder import (  # noqa: F401  (re-exported for the service entry point)
    BiEncoderReranker,
    _to_rerank_result,
)

__all__ = ["BiEncoderReranker", "_to_rerank_result"]
