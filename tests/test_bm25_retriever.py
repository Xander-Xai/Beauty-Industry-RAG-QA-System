"""BM25 Retriever 测试 — 查询构建、权限过滤、版本过滤。"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DEPLOYMENT_MODE"] = "development"

# torch mock shim
try:
    import torch  # noqa: F401
except ImportError:
    _fake_torch = types.ModuleType("torch")
    _fake_cuda = types.ModuleType("torch.cuda")
    _fake_cuda.is_available = lambda: False
    _fake_cuda.OutOfMemoryError = type("OutOfMemoryError", (Exception,), {})
    _fake_torch.cuda = _fake_cuda
    sys.modules["torch"] = _fake_torch
    sys.modules["torch.cuda"] = _fake_cuda


class TestBM25QueryBuilding:
    """测试 BM25 ES 查询构建逻辑。"""

    def _make_retriever(self):
        """创建不连接 ES 的 BM25Retriever。"""
        from retrieval.bm25_retriever import BM25Retriever

        r = BM25Retriever.__new__(BM25Retriever)
        r._es_client = None
        r.enabled = True
        r._es_version = (8, 0)
        return r

    def test_public_doc_query(self):
        """公开文档（role_mask=0）不需要脚本过滤。"""
        r = self._make_retriever()
        query_body = r._build_es_query("烟酰胺", user_role_mask=0, user_dept_mask=0, top_k=10)
        assert "query" in query_body
        assert query_body["size"] == 10
        # 公开用户不触发脚本过滤
        filters = query_body["query"]["bool"]["filter"]
        assert any(f.get("term", {}).get("status") == "active" for f in filters)

    def test_restricted_user_uses_script_filter(self):
        """ES >= 8.0 时，受限用户应使用 painless 脚本位运算过滤。"""
        r = self._make_retriever()
        r._es_version = (8, 0)
        query_body = r._build_es_query("法规", user_role_mask=5, user_dept_mask=3, top_k=20)
        filters = query_body["query"]["bool"]["filter"]
        # 应包含脚本过滤（role_mask 或 dept_mask 的位运算）
        script_found = False
        for f in filters:
            should = f.get("bool", {}).get("should", [])
            for s in should:
                if "script" in s:
                    script_found = True
                    # 检查是否包含位运算表达式
                    source = s["script"]["script"]["source"]
                    assert ".value &" in source
        assert script_found, "ES >= 8.0 应使用 painless 脚本过滤"

    def test_es_versions_use_bitmask_semantics(self):
        """Bitmask authorization must not degrade to exact role_bucket matching."""
        r = self._make_retriever()
        r._es_version = (7, 10)
        query_body = r._build_es_query("配方", user_role_mask=2, user_dept_mask=0, top_k=15)
        filters = query_body["query"]["bool"]["filter"]
        scripts = [
            clause["script"]["script"]["source"]
            for filter_clause in filters
            for clause in filter_clause.get("bool", {}).get("should", [])
            if "script" in clause
        ]
        assert any("role_mask" in source for source in scripts)
        assert all("role_bucket" not in str(filter_clause) for filter_clause in filters)

    def test_super_admin_skips_role_filter(self):
        """超级管理员不应添加角色脚本过滤。"""
        r = self._make_retriever()
        r._es_version = (8, 0)
        query_body = r._build_es_query("测试", user_role_mask=0xFFFFFFFF, user_dept_mask=0, top_k=10)
        filters = query_body["query"]["bool"]["filter"]
        for f in filters:
            should = f.get("bool", {}).get("should", [])
            for s in should:
                if "script" in s:
                    raise AssertionError("超级管理员不应有脚本过滤")

    def test_version_epoch_filter(self):
        """应包含 knowledge_version_epoch 过滤。"""
        r = self._make_retriever()
        # patch config
        import retrieval.bm25_retriever as mod

        old_epoch = mod.config.get("knowledge_version_epoch")
        mod.config["knowledge_version_epoch"] = "20260603_00"
        try:
            query_body = r._build_es_query("测试", 0, 0, 10)
            filters = query_body["query"]["bool"]["filter"]
            epoch_found = any(f.get("term", {}).get("doc_version_epoch") == "20260603_00" for f in filters)
            assert epoch_found, "应包含版本 epoch 过滤"
        finally:
            if old_epoch is not None:
                mod.config["knowledge_version_epoch"] = old_epoch

    def test_disabled_retriever_returns_empty(self):
        """禁用状态的检索器应返回空结果。"""
        r = self._make_retriever()
        r.enabled = False
        assert r.search("test", 0, 0) == []

    def test_invalid_role_mask_fails_closed(self):
        """Invalid authorization masks must not be coerced into a broader query."""
        r = self._make_retriever()
        import pytest

        with pytest.raises(ValueError):
            r._build_es_query("test", user_role_mask=-1, user_dept_mask=0, top_k=10)

    def test_dept_mask_filter(self):
        """非零 dept_mask 应添加部门过滤。"""
        r = self._make_retriever()
        r._es_version = (8, 0)
        query_body = r._build_es_query("测试", user_role_mask=0, user_dept_mask=4, top_k=10)
        filters = query_body["query"]["bool"]["filter"]
        dept_found = False
        for f in filters:
            should = f.get("bool", {}).get("should", [])
            for s in should:
                if s.get("script", {}).get("script", {}).get("params", {}).get("user_dept_mask") == 4:
                    dept_found = True
        assert dept_found, "非零 dept_mask 应添加部门过滤"
