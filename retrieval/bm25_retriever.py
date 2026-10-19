"""
BM25 关键词检索模块（readme 7.1 并行多路召回第 2 路）

基于 Elasticsearch 的 BM25 关键词精确匹配
支持权限与版本过滤下推

权限过滤策略：
- 使用 painless script_score 位运算过滤，无条件启用，依赖 ES 7.10+ 的 painless
  脚本能力（映射侧用 `role_mask` / `dept_mask` 位掩码字段）
- 没有版本回退路径：位运算过滤是唯一的过滤实现
"""

from __future__ import annotations

import logging
import os

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)


class BM25Retriever:
    """
    BM25 关键词检索器

    ES Fallback 与稀疏权限兜底（readme 7.1）：
    - 向量库不可用或召回有效文档数 < 50 时自动切换
    - 权限过滤统一走 painless 位运算，没有按 ES 版本分支的兜底实现

    **版本绑定（epoch binding）**：`_build_es_query` 会按配置的
    `knowledge_version_epoch` 过滤 `doc_version_epoch`。若索引里根本没有这个
    epoch，过滤条件匹配不到任何文档，接口会返回"没有相关结果"——而真实原因是
    "配置与索引版本不一致"。这两种情况对调用方完全一样，于是配置错误被读成
    检索失败。
    `probe_epoch_binding()` 把这个事实查出来，`_ensure_epoch_bound()` 在首次
    检索时消费它：绑定不成立时关闭词法检索路径并以 ERROR 级别记录具体是哪
    个 index、期望哪个 epoch、实际有哪些 epoch，而不是安静地返回空。
    """

    def __init__(self):
        self._es_client = None
        self._epoch_binding = None
        self.enabled = config.get("elasticsearch", {}).get("enabled", True)
        logger.info(f"BM25Retriever 初始化完成 (enabled={self.enabled})")

    @property
    def es_client(self):
        if self._es_client is None and self.enabled:
            try:
                from elasticsearch import Elasticsearch

                es_cfg = config.get("elasticsearch", {})
                kwargs = {"hosts": [es_cfg.get("host", "http://localhost:9200")]}
                # 优先从环境变量读取 ES 凭据，再回退到 config.json
                username = os.environ.get("ELASTICSEARCH_USERNAME") or es_cfg.get("username", "")
                password = os.environ.get("ELASTICSEARCH_PASSWORD") or es_cfg.get("password", "")
                if username and password:
                    kwargs["basic_auth"] = (username, password)
                self._es_client = Elasticsearch(**kwargs)
                # Probe connectivity once so an unreachable/misconfigured cluster
                # disables the lexical path instead of failing on every query.
                # The ES version is intentionally not stored: there is no
                # version-dependent query branch (see the class docstring).
                self._es_client.info()
                logger.info("ES 连接成功")
            except Exception as e:
                logger.warning(f"ES 连接失败: {e}")
                self.enabled = False
        return self._es_client

    def probe_epoch_binding(self, *, refresh: bool = False) -> dict:
        """检查配置的 epoch 是否真的存在于目标索引中。

        Read-only and cached: it is one aggregation, not a per-query cost. The
        returned dict is the evidence an operator needs to tell "this corpus has
        nothing relevant" apart from "this corpus was never asked for":

        ``{"bound": bool, "index": str, "configured_epoch": str, "present_epochs": [...],
        "document_count": int, "reason": str}``

        ``bound`` is true when the configured epoch is present *or* when no epoch
        is configured at all — an unconfigured epoch means no filter is applied,
        which is a deliberate configuration rather than a mismatch. An index with
        no documents is never "bound": it cannot answer anything, and reporting
        success there would hide an empty or wrong index.

        A client that does not implement the probe surface is also reported as
        bound, with a reason saying so: that is a fact about the caller, not
        about the index, and it must not take the lexical path down.
        """
        # ``getattr`` rather than a direct read: tests and integrations construct
        # a retriever via ``__new__`` to exercise query building without a
        # cluster, and a cached-probe fast path must not be the thing that breaks
        # them. Absence of the cache is simply "probe again".
        cached = getattr(self, "_epoch_binding", None)
        if cached is not None and not refresh:
            return cached

        index_name = str(config.get("elasticsearch", {}).get("index", "") or "")
        configured_epoch = str(config.get("knowledge_version_epoch", "") or "")
        verdict = {
            "index": index_name,
            "configured_epoch": configured_epoch,
            "present_epochs": [],
            "document_count": 0,
            "bound": False,
            "reason": "",
        }

        if not self.enabled or self.es_client is None:
            verdict["reason"] = "Elasticsearch client is unavailable, so the index cannot be inspected"
            self._epoch_binding = verdict
            return verdict
        if not index_name:
            verdict["reason"] = "no elasticsearch.index is configured"
            self._epoch_binding = verdict
            return verdict

        try:
            count = int(self.es_client.count(index=index_name).get("count", 0))
            verdict["document_count"] = count
            response = self.es_client.search(
                index=index_name,
                body={
                    "size": 0,
                    "aggs": {"epochs": {"terms": {"field": "doc_version_epoch", "size": 50}}},
                },
            )
            present = [
                str(bucket.get("key"))
                for bucket in (response.get("aggregations", {}).get("epochs", {}) or {}).get("buckets", []) or ()
            ]
            verdict["present_epochs"] = present
        except (AttributeError, NotImplementedError, TypeError) as exc:
            # The client does not implement the probe surface at all — it is a
            # purpose-built adapter, not an Elasticsearch client. That is a fact
            # about the *caller*, not about the index, and it is not evidence
            # that the corpus is unbound. Disabling the production lexical path
            # because an adapter stub lacks `count` would be this change breaking
            # its caller rather than protecting anyone. Recorded, not fatal.
            verdict["bound"] = True
            verdict["reason"] = f"epoch probe unsupported by this client ({type(exc).__name__}); query unverified"
            self._epoch_binding = verdict
            return verdict
        except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
            verdict["reason"] = f"could not inspect index {index_name!r}: {type(exc).__name__}: {exc}"
            self._epoch_binding = verdict
            return verdict

        if count == 0:
            verdict["reason"] = f"index {index_name!r} contains no documents"
        elif not configured_epoch:
            verdict["bound"] = True
            verdict["reason"] = f"no knowledge_version_epoch configured; index {index_name!r} holds {list(present)}"
        elif configured_epoch in present:
            verdict["bound"] = True
            verdict["reason"] = f"epoch {configured_epoch!r} is present in {index_name!r}"
        else:
            verdict["reason"] = (
                f"index {index_name!r} holds epoch(s) {list(present)} but knowledge_version_epoch is "
                f"{configured_epoch!r}; the retrieval filter would match none of its documents, so every "
                "query would read as 'no results' instead of 'wrong corpus configured'"
            )
        self._epoch_binding = verdict
        return verdict

    def _ensure_epoch_bound(self) -> bool:
        """Refuse to query an index that does not hold the configured epoch.

        Disabling the lexical path is the same response the retriever already
        gives an unreachable cluster, and it is deliberate: continuing would
        return an empty result list that reads as "nothing relevant exists".
        Failing loudly is the only way the misconfiguration becomes visible
        before someone concludes the corpus is simply small.
        """
        verdict = self.probe_epoch_binding()
        if verdict.get("bound"):
            return True
        if self.enabled:
            logger.error(f"BM25 epoch binding failed, lexical path disabled: {verdict.get('reason', '')}")
            self.enabled = False
        return False

    def search(
        self,
        query: str,
        user_role_mask: int,
        user_dept_mask: int,
        top_k: int = 50,
    ) -> list[dict]:
        """
        BM25 关键词检索

        Args:
            query: 查询文本
            user_role_mask: 用户角色位掩码
            user_dept_mask: 用户部门位掩码
            top_k: 召回数量

        Returns:
            [{"doc_id": str, "content": str, "score": float, "metadata": dict}]
        """
        if not self.enabled or self.es_client is None:
            return []
        if not self._ensure_epoch_bound():
            return []

        try:
            query_body = self._build_es_query(query, user_role_mask, user_dept_mask, top_k)
            response = self.es_client.search(
                index=config["elasticsearch"]["index"],
                body=query_body,
            )

            hits = []
            for hit in response["hits"]["hits"]:
                source = hit.get("_source", {})
                # Expose the source-level doc_id (matches Qdrant) so RRF merges
                # chunks from the same document. Fall back to the ES _id only
                # for legacy rows written without a doc_id field.
                hits.append(
                    {
                        "doc_id": source.get("doc_id") or hit["_id"],
                        "content": source.get("content", ""),
                        "score": hit["_score"],
                        "metadata": {
                            "doc_type": source.get("doc_type", ""),
                            "law_id": source.get("law_id", ""),
                            "chunk_id": source.get("chunk_id"),
                            "role_mask": source.get("role_mask"),
                            "dept_mask": source.get("dept_mask"),
                        },
                    }
                )
            return hits

        except Exception as e:
            logger.error(f"BM25 ES 检索失败: {e}")
            return []

    def _build_es_query(
        self,
        query: str,
        user_role_mask: int,
        user_dept_mask: int,
        top_k: int,
    ) -> dict:
        """
        构建 ES Bool 查询（含权限过滤下推）

        Role/dept bitmask checks use parameterized Painless filters. There is no
        ES-version branch and no precomputed bucket fallback: this is the only
        filter implementation, and records missing either mask are excluded.
        """
        must_filters = [
            {"term": {"status": "active"}},
        ]

        # Invalid authorization context must fail closed rather than being
        # coerced into a broader anonymous query.
        _MAX_UINT32 = 0xFFFFFFFF
        if (
            type(user_role_mask) is not int
            or not 0 <= user_role_mask <= _MAX_UINT32
            or type(user_dept_mask) is not int
            or not 0 <= user_dept_mask <= _MAX_UINT32
        ):
            raise ValueError("authorization masks must be uint32 integers")

        # Missing permission fields are not equivalent to public access.
        must_filters.extend(
            [
                {"exists": {"field": "role_mask"}},
                {"exists": {"field": "dept_mask"}},
            ]
        )

        from common.auth import is_admin_role_mask

        if not is_admin_role_mask(user_role_mask):
            must_filters.extend(
                [
                    {
                        "bool": {
                            "should": [
                                {"term": {"role_mask": 0}},
                                {
                                    "script": {
                                        "script": {
                                            "source": "doc['role_mask'].size() != 0 && (doc['role_mask'].value & params.user_role_mask) != 0",
                                            "lang": "painless",
                                            "params": {"user_role_mask": user_role_mask},
                                        }
                                    }
                                },
                            ],
                            "minimum_should_match": 1,
                        }
                    },
                    {
                        "bool": {
                            "should": [
                                {"term": {"dept_mask": 0}},
                                {
                                    "script": {
                                        "script": {
                                            "source": "doc['dept_mask'].size() != 0 && (doc['dept_mask'].value & params.user_dept_mask) != 0",
                                            "lang": "painless",
                                            "params": {"user_dept_mask": user_dept_mask},
                                        }
                                    }
                                },
                            ],
                            "minimum_should_match": 1,
                        }
                    },
                ]
            )

        # 版本过滤
        active_epoch = config.get("knowledge_version_epoch", "")
        if active_epoch:
            must_filters.append({"term": {"doc_version_epoch": active_epoch}})

        return {
            "query": {
                "bool": {
                    "must": [
                        {"match": {"content": {"query": query, "boost": 1.0}}},
                    ],
                    "filter": must_filters,
                }
            },
            "size": top_k,
        }

    def _build_es_fallback_query(
        self,
        query: str,
        user_role_mask: int,
        user_dept_mask: int,
        top_k: int,
    ) -> dict:
        """Use the same authorization filters for fallback as regular BM25."""
        return self._build_es_query(query, user_role_mask, user_dept_mask, top_k)

    def fallback_search(
        self,
        query: str,
        user_role_mask: int,
        user_dept_mask: int,
        top_k: int = 100,
    ) -> list[dict]:
        """
        ES Fallback 检索；复用常规 BM25 的身份及权限过滤。

        readme 7.1: 当向量库不可用或召回有效文档数 < 50 时自动切换
        """
        if not self.enabled or self.es_client is None:
            return []
        if not self._ensure_epoch_bound():
            return []

        try:
            query_body = self._build_es_fallback_query(query, user_role_mask, user_dept_mask, top_k)
            response = self.es_client.search(
                index=config["elasticsearch"]["index"],
                body=query_body,
            )
            return [
                {
                    "doc_id": hit.get("_source", {}).get("doc_id") or hit["_id"],
                    "content": hit.get("_source", {}).get("content", ""),
                    "score": hit["_score"],
                    "metadata": {
                        "chunk_id": hit.get("_source", {}).get("chunk_id"),
                        "role_mask": hit.get("_source", {}).get("role_mask"),
                        "dept_mask": hit.get("_source", {}).get("dept_mask"),
                    },
                }
                for hit in response["hits"]["hits"]
            ]
        except Exception as e:
            logger.error(f"ES Fallback 检索失败: {e}")
            return []
