"""
BM25 关键词检索模块（readme 7.1 并行多路召回第 2 路）

基于 Elasticsearch 的 BM25 关键词精确匹配
支持权限与版本过滤下推

权限过滤策略：
- ES >= 8.0: 使用 painless script_score 位运算过滤
- ES < 8.0: 使用预计算的 role_bucket 字段 + terms 过滤（兜底）
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
    - 若 ES 版本不支持位运算脚本，使用预计算的 role_bucket 字段进行 terms 过滤
    """

    def __init__(self):
        self._es_client = None
        self.enabled = config.get("elasticsearch", {}).get("enabled", True)
        self._es_version = None
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
                # 获取 ES 版本
                self._es_version = tuple(map(int, self._es_client.info()["version"]["number"].split(".")[:2]))
                logger.info(f"ES 连接成功: version={self._es_version}")
            except Exception as e:
                logger.warning(f"ES 连接失败: {e}")
                self.enabled = False
        return self._es_client

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

        Role/dept bitmask checks use parameterized Painless filters on supported
        Elasticsearch versions. Records missing either mask are excluded.
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

    def _super_admin_mask(self) -> int:
        return config.get("rbac", {}).get("super_admin_mask", 0xFFFFFFFF)

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
