"""
Bitmask RBAC 权限判定测试 (auth/bitmask_rbac.py)

覆盖 §3.5 权限控制逻辑：
- 公开文档 (role_mask=0) 的访问控制
- 超级管理员 (0xFFFFFFFF) 绕过
- 普通 RBAC 位与匹配
- 部门掩码匹配
- Qdrant Filter 生成
- 编码函数 (encode_role_mask / encode_dept_mask)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



# ── 使用 bitmask_rbac 模块（依赖 config.json 已在项目根目录） ──

from auth.bitmask_rbac import build_qdrant_filter, encode_dept_mask, encode_role_mask, is_allowed

# ── 公开文档访问控制 ──

class TestPublicDocAccess:
    """公开文档 (doc_role_mask=0) 应对所有用户开放"""

    def test_public_doc_with_zero_dept_mask(self):
        """公开文档 + 无部门限制：任何用户均可访问"""
        assert is_allowed(dr=0, ur=1, dd=0, ud=0) is True
        assert is_allowed(dr=0, ur=0, dd=0, ud=0) is True

    def test_public_doc_with_dept_mask_user_matches(self):
        """公开文档有部门限制 + 用户部门匹配：允许访问"""
        # doc_dept_mask=2 (quality_dept), user_dept_mask=2 -> 2&2=2 !=0
        assert is_allowed(dr=0, ur=0, dd=2, ud=2) is True

    def test_public_doc_with_dept_mask_user_no_match(self):
        """公开文档有部门限制 + 用户部门不匹配：拒绝访问"""
        # doc_dept_mask=2, user_dept_mask=1 -> 2&1=0
        assert is_allowed(dr=0, ur=0, dd=2, ud=1) is False

    def test_public_doc_with_dept_mask_user_zero(self):
        """公开文档有部门限制 + 用户无部门信息：拒绝访问"""
        assert is_allowed(dr=0, ur=0, dd=2, ud=0) is False


# ── 超级管理员绕过 ──

class TestSuperAdmin:
    """超级管理员 (role_mask=0xFFFFFFFF=4294967295) 应绕过所有权限检查"""

    def test_super_admin_accesses_restricted_doc(self):
        """超管可访问任何受保护文档"""
        # dr=8 (sales only), ur=0xFFFFFFFF (super admin)
        assert is_allowed(dr=8, ur=4294967295, dd=0, ud=0) is True

    def test_super_admin_accesses_dept_restricted_doc(self):
        """超管可访问有部门限制的文档"""
        assert is_allowed(dr=4, ur=4294967295, dd=8, ud=0) is True

    def test_super_admin_accesses_tightly_restricted_doc(self):
        """超管可访问同时有角色和部门限制的文档"""
        assert is_allowed(dr=7, ur=4294967295, dd=15, ud=0) is True


# ── 普通 RBAC 位与匹配 ──

class TestNormalRBAC:
    """普通角色/部门的位与匹配逻辑"""

    def test_exact_role_match(self):
        """用户角色位与文档角色位精确匹配：允许"""
        # dr=1 (rd), ur=1 (rd) -> 1&1=1 !=0
        assert is_allowed(dr=1, ur=1, dd=0, ud=0) is True

    def test_combined_role_match(self):
        """用户多角色中有一个匹配文档角色：允许"""
        # dr=1 (rd), ur=3 (rd+quality) -> 1&3=1 !=0
        assert is_allowed(dr=1, ur=3, dd=0, ud=0) is True

    def test_role_no_match(self):
        """用户角色位与文档角色位无交集：拒绝"""
        # dr=4 (regulation), ur=1 (rd) -> 4&1=0
        assert is_allowed(dr=4, ur=1, dd=0, ud=0) is False

    def test_role_match_dept_no_match(self):
        """角色匹配但部门不匹配：拒绝"""
        # dr=1, ur=1 -> role ok. dd=2, ud=1 -> 2&1=0 -> dept not ok
        assert is_allowed(dr=1, ur=1, dd=2, ud=1) is False

    def test_role_match_dept_zero_means_no_restriction(self):
        """角色匹配 + 文档无部门限制 (dd=0)：允许"""
        assert is_allowed(dr=1, ur=1, dd=0, ud=0) is True

    def test_role_match_dept_match(self):
        """角色匹配且部门匹配：允许"""
        # dr=1, ur=1 -> role ok. dd=2, ud=2 -> 2&2=2 -> dept ok
        assert is_allowed(dr=1, ur=1, dd=2, ud=2) is True

    def test_admin_role_has_all_bits(self):
        """admin 角色 (mask=0x7FFFFFFF) 拥有所有低 31 位"""
        # admin = 2147483647 = 0x7FFFFFFF, 对任何 dr 都能匹配
        # 除了 dr=0 走公开路径
        assert is_allowed(dr=4, ur=2147483647, dd=0, ud=0) is True
        assert is_allowed(dr=1, ur=2147483647, dd=0, ud=0) is True
        assert is_allowed(dr=8, ur=2147483647, dd=0, ud=0) is True

    def test_zero_role_cannot_access_restricted_doc(self):
        """无角色 (ur=0) 无法访问有角色限制的文档"""
        assert is_allowed(dr=1, ur=0, dd=0, ud=0) is False


# ── encode_role_mask / encode_dept_mask ──

class TestEncodeMasks:
    """验证角色/部门名称到 bitmask 的编码"""

    def test_encode_single_role(self):
        """编码单个角色"""
        assert encode_role_mask(["rd"]) == 1
        assert encode_role_mask(["quality"]) == 2
        assert encode_role_mask(["regulation"]) == 4
        assert encode_role_mask(["sales"]) == 8

    def test_encode_multiple_roles(self):
        """编码多角色，结果为各角色位的 OR"""
        assert encode_role_mask(["rd", "quality"]) == 3  # 1 | 2
        assert encode_role_mask(["rd", "regulation"]) == 5  # 1 | 4
        assert encode_role_mask(["rd", "quality", "regulation"]) == 7  # 1|2|4

    def test_encode_admin_role(self):
        """admin 角色编码为 0x7FFFFFFF"""
        assert encode_role_mask(["admin"]) == 2147483647

    def test_encode_unknown_role(self):
        """未知角色名被忽略"""
        assert encode_role_mask(["unknown_role"]) == 0

    def test_encode_empty_roles(self):
        """空列表编码为 0"""
        assert encode_role_mask([]) == 0

    def test_encode_single_dept(self):
        """编码单个部门"""
        assert encode_dept_mask(["rd_dept"]) == 1
        assert encode_dept_mask(["quality_dept"]) == 2
        assert encode_dept_mask(["regulation_dept"]) == 4
        assert encode_dept_mask(["sales_dept"]) == 8

    def test_encode_multiple_depts(self):
        """编码多部门"""
        assert encode_dept_mask(["rd_dept", "quality_dept"]) == 3

    def test_encode_all_dept(self):
        """'all' 部门编码为 0（无限制）"""
        assert encode_dept_mask(["all"]) == 0


# ── Qdrant Filter 生成 ──

class TestQdrantFilter:
    """验证 build_qdrant_filter 返回 Qdrant Filter 对象"""

    def test_returns_filter_object(self):
        """build_qdrant_filter 返回 Qdrant Filter 对象"""
        from auth.bitmask_rbac import build_qdrant_filter
        from qdrant_client.http.models import Filter
        f = build_qdrant_filter(ur=1, ue=2, ae="20260601")
        assert isinstance(f, Filter)

    def test_filter_contains_status_active(self):
        """Filter 包含 status == active 条件"""
        from auth.bitmask_rbac import build_qdrant_filter
        from qdrant_client.http.models import FieldCondition, MatchValue
        f = build_qdrant_filter(ur=1, ue=0, ae="v1")
        assert f.must is not None
        status_cond = any(
            c.key == "status" and isinstance(c.match, MatchValue) and c.match.value == "active"
            for c in f.must
        )
        assert status_cond, "Filter 应包含 status == active"

    def test_filter_input_validation(self):
        """非法输入应抛出 ValueError"""
        from auth.bitmask_rbac import build_qdrant_filter

        import pytest
        with pytest.raises(ValueError):
            build_qdrant_filter(ur=-1, ue=0, ae="v1")
        with pytest.raises(ValueError):
            build_qdrant_filter(ur=1, ue=0, ae="invalid/epoch")
        with pytest.raises(ValueError):
            build_qdrant_filter(ur="bad", ue=0, ae="v1")

    def test_filter_validation_passes(self):
        """合法输入不抛出异常"""
        from auth.bitmask_rbac import build_qdrant_filter
        from qdrant_client.http.models import Filter
        f = build_qdrant_filter(ur=1, ue=1, ae="v1")
        assert isinstance(f, Filter)
