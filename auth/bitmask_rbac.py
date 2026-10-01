import logging
import re

logger = logging.getLogger(__name__)

# 输入验证正则：仅允许字母、数字、下划线、连字符（用于 knowledge_version_epoch）
_SAFE_VERSION_PATTERN = re.compile(r'^[a-zA-Z0-9_\-]+$')

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

_ADMIN = _ROLE.get("admin")


def is_allowed(dr, ur, dd, ud):
    if dr == 0:
        if dd == 0:
            return True
        return (dd & ud) != 0
    if ur in {_SUPER, _ADMIN}:
        return True
    role_ok = (dr & ur) != 0
    dept_ok = dd == 0 or (dd & ud) != 0
    return role_ok and dept_ok


def build_qdrant_filter(ur: int, ue: int, ae: str):
    """
    构建 Qdrant Filter 对象。

    Qdrant pre-filter 不支持位掩码运算（RBAC），因此仅处理：
    - status == 'active'（标量精准匹配）
    - doc_version_epoch == active_epoch（知识版本隔离）

    RBAC 权限过滤在 Python 层通过 is_allowed() 后置执行。

    安全：对所有输入进行类型和范围验证，防止过滤器注入。
    """
    # 输入验证：确保整数在 uint32 范围内
    if not isinstance(ur, int) or not (0 <= ur <= _MAX_UINT32):
        raise ValueError(f"user_role_mask 必须为 uint32 整数，收到: {ur!r}")
    if not isinstance(ue, int) or not (0 <= ue <= _MAX_UINT32):
        raise ValueError(f"user_dept_mask 必须为 uint32 整数，收到: {ue!r}")

    # 输入验证：knowledge_version_epoch 仅允许安全字符
    ae_str = str(ae)
    if not _SAFE_VERSION_PATTERN.match(ae_str):
        raise ValueError(f"knowledge_version_epoch 包含非法字符: {ae_str!r}")

    from qdrant_client.http.models import FieldCondition, Filter, MatchValue

    must_conditions = [
        FieldCondition(key="status", match=MatchValue(value="active")),
    ]
    # "default" 表示尚未启用版本切换；强制过滤会让现有非 default
    # 数据全部不可见。只有显式激活版本时才下推 epoch 条件。
    if ae_str != "default":
        must_conditions.append(
            FieldCondition(
                key="doc_version_epoch",
                match=MatchValue(value=ae_str),
            )
        )

    return Filter(must=must_conditions)

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
