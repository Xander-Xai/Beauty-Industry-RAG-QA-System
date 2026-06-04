"""
Bitmask RBAC 权限判定测试 (auth/bitmask_rbac.py)

覆盖 §3.5 权限控制逻辑：
- 公开文档 (role_mask=0) 的访问控制
- 超级管理员 (0xFFFFFFFF) 绕过
- 普通 RBAC 位与匹配
- 部门掩码匹配
- Milvus 过滤表达式生成
- 编码函数 (encode_role_mask / encode_dept_mask)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ── 使用 bitmask_rbac 模块（依赖 config.json 已在项目根目录） ──

from auth.bitmask_rbac import is_allowed, encode_role_mask, encode_dept_mask, build_milvus_filter


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


# ── Milvus 过滤表达式生成 ──

class TestMilvusFilter:
    """验证 Milvus 布尔过滤表达式的正确性"""

    def test_filter_contains_role_check(self):
        """过滤表达式包含 role_mask 检查"""
        f = build_milvus_filter(ur=1, ue=2, ae="20260601")
        assert "role_mask == 0" in f
        assert "role_mask & 1" in f  # 新格式含空格

    def test_filter_contains_dept_check(self):
        """过滤表达式包含 dept_mask 检查"""
        f = build_milvus_filter(ur=1, ue=2, ae="20260601")
        assert "dept_mask == 0" in f
        assert "dept_mask & 2" in f  # 新格式含空格

    def test_filter_contains_epoch(self):
        """过滤表达式包含版本 epoch"""
        f = build_milvus_filter(ur=1, ue=0, ae="20260615_01")
        assert "20260615_01" in f

    def test_filter_contains_status_active(self):
        """过滤表达式包含 status == active"""
        f = build_milvus_filter(ur=1, ue=0, ae="v1")
        assert "status == 'active'" in f

    def test_filter_uses_and_or_operators(self):
        """过滤表达式使用 AND/OR 逻辑连接各条件"""
        f = build_milvus_filter(ur=1, ue=1, ae="v1")
        assert "OR" in f
        assert "AND" in f

    def test_filter_zero_masks(self):
        """零掩码过滤表达式：公开文档 + 全部门"""
        f = build_milvus_filter(ur=0, ue=0, ae="v1")
        # role_mask=0 OR ((role_mask & 0) != 0) -> 公开文档路径
        assert "role_mask & 0" in f  # 新格式含空格
        assert "dept_mask & 0" in f
