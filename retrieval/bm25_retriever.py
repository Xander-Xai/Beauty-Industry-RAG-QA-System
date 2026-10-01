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
                hits.append({
                    "doc_id": hit["_id"],
                    "content": hit["_source"].get("content", ""),
                    "score": hit["_score"],
                    "metadata": {
                        "doc_type": hit["_source"].get("doc_type", ""),
                        "law_id": hit["_source"].get("law_id", ""),
                        "role_mask": hit["_source"].get("role_mask", 0),
                        "dept_mask": hit["_source"].get("dept_mask", 0),
                        "doc_version_epoch": hit["_source"].get("doc_version_epoch", ""),
                    },
                })
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

        权限过滤策略：
        - ES >= 8.0: painless script_score 位运算过滤
        - ES < 8.0: role_bucket + dept_bucket 字段 + terms 过滤
        """
        must_filters = [
            {"term": {"status": "active"}},
        ]

        # 输入验证：确保整数类型和范围
        _MAX_UINT32 = 0xFFFFFFFF
        if not isinstance(user_role_mask, int) or not (0 <= user_role_mask <= _MAX_UINT32):
            logger.error("user_role_mask 类型或范围无效: %r", user_role_mask)
            user_role_mask = 0
        if not isinstance(user_dept_mask, int) or not (0 <= user_dept_mask <= _MAX_UINT32):
            logger.error("user_dept_mask 类型或范围无效: %r", user_dept_mask)
            user_dept_mask = 0

        # 判断 ES 版本选择过滤策略
        use_script_filter = self._es_version is not None and self._es_version >= (8, 0)

        is_admin = user_role_mask in {
            self._super_admin_mask(),
            config.get("rbac", {}).get("roles", {}).get("admin"),
        }
        if not is_admin:
            if use_script_filter:
                # ES >= 8.0: painless script 位运算过滤
                must_filters.append({
                    "bool": {
                        "should": [
                            {"term": {"role_mask": 0}},  # 公开文档
                            {"script": {
                                "script": {
                                    "source": f"doc['role_mask'].value & {user_role_mask} != 0",
                                    "lang": "painless",
                                }
                            }},
                        ],
                        "minimum_should_match": 1,
                    }
                })

                must_filters.append({
                    "bool": {
                        "should": [
                            {"term": {"dept_mask": 0}},  # 全部门可见
                            {"script": {
                                "script": {
                                    "source": f"doc['dept_mask'].value & {user_dept_mask} != 0",
                                    "lang": "painless",
                                }
                            }},
                        ],
                        "minimum_should_match": 1,
                    }
                })
            else:
                # ES < 8.0: 使用预计算的 role_bucket / dept_bucket 字段
                must_filters.append({
                    "bool": {
                        "should": [
                            {"term": {"role_bucket": user_role_mask}},
                            {"term": {"role_mask": 0}},  # 兜底公开文档
                        ],
                        "minimum_should_match": 1,
                    }
                })
                must_filters.append({
                    "bool": {
                        "should": [
                            {"term": {"dept_bucket": user_dept_mask}},
                            {"term": {"dept_mask": 0}},
                        ],
                        "minimum_should_match": 1,
                    }
                })

        # 版本过滤
        active_epoch = config.get("knowledge_version_epoch", "")
        if active_epoch and active_epoch != "default":
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

    def fallback_search(
        self,
        query: str,
        user_role_mask: int = 0,
        user_dept_mask: int = 0,
        top_k: int = 100,
    ) -> list[dict]:
        """
        ES Fallback 检索（复用主BM25的权限和版本过滤）

        readme 7.1: 当向量库不可用或召回有效文档数 < 50 时自动切换
        """
        return self.search(
            query=query,
            user_role_mask=user_role_mask,
            user_dept_mask=user_dept_mask,
            top_k=top_k,
        )
