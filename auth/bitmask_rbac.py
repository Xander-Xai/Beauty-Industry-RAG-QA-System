import logging
import re

logger = logging.getLogger(__name__)

# 输入验证正则：仅允许字母、数字、下划线、连字符（用于 knowledge_version_epoch）
_SAFE_VERSION_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")

# 输入验证：整数范围校验（32位无符号）
_MAX_UINT32 = 0xFFFFFFFF

# 优先使用 common.config 统一配置，失败时使用最小安全默认值
try:
    from common.config import get_config as _get_config

    _cfg = _get_config()
    _ROLE = _cfg.rbac.roles
    _DEPT = _cfg.rbac.departments
    _SUPER = _cfg.rbac.super_admin_mask
except Exception:
    _ROLE = {"admin": 2147483647}
    _DEPT = {"all": 0}
    _SUPER = 4294967295


def is_allowed(dr, ur, dd, ud):
    """Compatibility wrapper for the canonical service-layer RBAC predicate."""
    from common.auth import is_allowed as _is_allowed

    return _is_allowed(dr, ur, dd, ud)


def build_qdrant_filter(ur: int, ue: int, ae: str):
    """
    构建 Qdrant Filter 对象。

    Qdrant pre-filter 不支持位掩码运算（RBAC），因此仅处理状态和版本：
    - status == 'active'（标量精准匹配）
    - doc_version_epoch == 当前知识库 epoch

    RBAC 权限过滤仍在 Python 层通过 is_allowed() 后置执行。

    安全：对所有输入进行类型和范围验证，防止过滤器注入。
    """
    # 输入验证：确保整数在 uint32 范围内
    if type(ur) is not int or not (0 <= ur <= _MAX_UINT32):
        raise ValueError(f"user_role_mask 必须为 uint32 整数，收到: {ur!r}")
    if type(ue) is not int or not (0 <= ue <= _MAX_UINT32):
        raise ValueError(f"user_dept_mask 必须为 uint32 整数，收到: {ue!r}")

    # 输入验证：knowledge_version_epoch 仅允许安全字符
    ae_str = str(ae)
    if not _SAFE_VERSION_PATTERN.match(ae_str):
        raise ValueError(f"knowledge_version_epoch 包含非法字符: {ae_str!r}")

    from qdrant_client.http.models import FieldCondition, Filter, MatchValue

    return Filter(
        must=[
            FieldCondition(key="status", match=MatchValue(value="active")),
            FieldCondition(key="doc_version_epoch", match=MatchValue(value=ae_str)),
        ]
    )


def build_qdrant_image_filter(ur: int, ue: int):
    """Build the legacy CLIP image filter without the text-only epoch field."""
    if type(ur) is not int or not (0 <= ur <= _MAX_UINT32):
        raise ValueError(f"user_role_mask 必须为 uint32 整数，收到: {ur!r}")
    if type(ue) is not int or not (0 <= ue <= _MAX_UINT32):
        raise ValueError(f"user_dept_mask 必须为 uint32 整数，收到: {ue!r}")

    from qdrant_client.http.models import FieldCondition, Filter, MatchValue

    return Filter(must=[FieldCondition(key="status", match=MatchValue(value="active"))])


def encode_role_mask(roles):
    m = 0
    for r in roles:
        if r in _ROLE:
            m = m | _ROLE[r]
    return m


def encode_dept_mask(depts):
    m = 0
    for d in depts:
        if d in _DEPT:
            m = m | _DEPT[d]
    return m
