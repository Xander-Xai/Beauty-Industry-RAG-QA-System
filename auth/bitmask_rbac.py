import json
import logging
import os

logger = logging.getLogger(__name__)

# 优先使用 common.config 统一配置，回退到直接读取 config.json
try:
    from common.config import get_config as _get_config
    _cfg = _get_config()
    _ROLE = _cfg.rbac.roles
    _DEPT = _cfg.rbac.departments
    _SUPER = _cfg.rbac.super_admin_mask
except Exception:
    _config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    with open(_config_path, encoding="utf-8") as f:
        config = json.load(f)
    _ROLE = config["rbac"]["roles"]
    _DEPT = config["rbac"]["departments"]
    _SUPER = config["rbac"]["super_admin_mask"]


def is_allowed(dr, ur, dd, ud):
    if dr == 0:
        if dd == 0:
            return True
        return (dd & ud) != 0
    if ur == _SUPER:
        return True
    role_ok = (dr & ur) != 0
    dept_ok = dd == 0 or (dd & ud) != 0
    return role_ok and dept_ok


def build_milvus_filter(ur, ue, ae):
    """
    构建 Milvus 布尔过滤表达式，包含 RBAC 权限和版本门控。

    修复：使用显式括号确保 AND 优先于 OR 的正确语义：
        (role_mask == 0 OR ((role_mask & user_role) != 0))
        AND (dept_mask == 0 OR ((dept_mask & user_dept) != 0))
        AND doc_version_epoch == '{epoch}'
        AND status == 'active'
    """
    rm0 = "(role_mask == 0)"
    rmu = f"((role_mask & {ur}) != 0)"
    dm0 = "(dept_mask == 0)"
    dmu = f"((dept_mask & {ue}) != 0)"
    ep = f"doc_version_epoch == '{ae}'"
    st = "status == 'active'"
    return f"({rm0} OR {rmu}) AND ({dm0} OR {dmu}) AND {ep} AND {st}" 
 
def encode_role_mask(roles): 
    m = 0 
    for r in roles: 
        if r in _ROLE: m = m | _ROLE[r] 
    return m 
 
def encode_dept_mask(depts): 
    m = 0 
    for d in depts: 
        if d in _DEPT: m = m | _DEPT[d] 
    return m
