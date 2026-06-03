import json 
with open('config.json', encoding='utf-8') as f: 
    config = json.load(f) 
 
_ROLE = config['rbac']['roles'] 
_DEPT = config['rbac']['departments'] 
_SUPER = config['rbac']['super_admin_mask'] 
 
def is_allowed(dr, ur, dd, ud): 
    if dr == 0: 
        if dd == 0: return True 
        return (dd & ud) != 0 
    if ur == _SUPER: return True 
    role_ok = (dr & ur) != 0 
    dept_ok = dd == 0 or (dd & ud) != 0 
    return role_ok and dept_ok 
 
def build_milvus_filter(ur, ue, ae): 
    c = chr 
    l, r, a, q = c(40), c(41), c(38), c(39) 
    rm0 = l + 'role_mask == 0' + r 
    rmu = l + l + 'role_mask' + a + str(ur) + r + ' != 0' + r 
    dm0 = l + 'dept_mask == 0' + r 
    dmu = l + l + 'dept_mask' + a + str(ue) + r + ' != 0' + r 
    ep = 'doc_version_epoch == ' + q + ae + q 
    st = 'status == ' + q + 'active' + q 
    return rm0 + ' OR ' + rmu + ' AND ' + dm0 + ' OR ' + dmu + ' AND ' + ep + ' AND ' + st 
 
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
