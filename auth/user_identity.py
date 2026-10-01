"""
用户身份解析模块

从请求中解析用户身份，填充 RequestContext 的权限掩码
支持 JWT Token / API Key / Session Cookie / 开发模式 Header
"""

from __future__ import annotations

import logging
import os
import time

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)

# JWT 配置
JWT_SECRET = os.environ.get("JWT_SECRET") or config.get("auth", {}).get("jwt_secret", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = config.get("auth", {}).get("jwt_expiry_hours", 24)

# 生产模式安全检查
_deploy_mode = config.get("deployment_mode", "development")
if _deploy_mode == "production":
    if not JWT_SECRET or JWT_SECRET.startswith("dev-"):
        raise RuntimeError("生产模式下必须设置强 JWT_SECRET 环境变量（不得以 'dev-' 开头）")
    # H-2 修复: 生产模式下禁止 dev_mode
    if config.get("auth", {}).get("dev_mode", False):
        raise RuntimeError("生产模式下不允许 dev_mode=true")


class UserIdentity:
    """
    用户身份解析器

    从认证凭证中提取：
    - user_id
    - user_role_mask (int32 位图)
    - user_dept_mask (int32 位图)

    支持的认证方式（按优先级）：
    1. JWT Bearer Token（生产环境）
    2. 开发模式 Header（开发环境）
    3. 演示模式（测试用）
    """

    def __init__(self):
        self.roles = config["rbac"]["roles"]
        self.departments = config["rbac"]["departments"]
        self.super_admin_mask = config["rbac"]["super_admin_mask"]
        self.dev_mode = config.get("auth", {}).get("dev_mode", True)
        logger.info(f"UserIdentity 初始化完成 (dev_mode={self.dev_mode})")

    def parse_from_request(self, request) -> dict:
        """
        从 Flask request 中解析用户身份

        优先级：
        1. Authorization: Bearer <jwt_token> → JWT 解析
        2. X-User-* Headers → 开发模式直接读取
        3. 兜底 → anonymous / super_admin

        Returns:
            {"user_id": str, "user_role_mask": int, "user_dept_mask": int}
        """
        # 优先尝试 JWT 解析
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            result = self.parse_from_token(token)
            if result is not None:
                return result
            # JWT 解析失败，降级到 header 模式
            logger.warning("JWT 解析失败，降级到开发模式 Header")

        # 开发模式：从请求头直接读取
        # H-2 修复: 默认角色掩码改为 0（零权限），不再默认 super_admin
        if self.dev_mode:
            return {
                "user_id": request.headers.get("X-User-ID", "anonymous"),
                "user_role_mask": int(request.headers.get("X-Role-Mask", 0)),
                "user_dept_mask": int(request.headers.get("X-Dept-Mask", 0)),
            }

        # 非开发模式且无有效认证 → 拒绝
        return {
            "user_id": "anonymous",
            "user_role_mask": 0,
            "user_dept_mask": 0,
        }

    def parse_from_token(self, token: str) -> dict | None:
        """
        从 JWT Token 解析用户身份（PRD §11 — 统一 RS256 验证）

        优先使用 jwt_auth.verify_token (RS256) 验证，
        失败则回退到 HS256（向后兼容）。

        JWT Payload 格式（由认证服务签发）:
        {
            "sub": "user_id",
            "user_id": "user_001",
            "role_mask": 5,
            "dept_mask": 3,
            "roles": ["rd", "quality"],
            "depts": ["rd_dept"],
            "exp": 1717400000,
            "iat": 1717300000
        }

        Returns:
            {"user_id": str, "user_role_mask": int, "user_dept_mask": int} 或 None
        """
        # 优先尝试 RS256 验证（PRD §11 统一 JWT）
        try:
            from auth.jwt_auth import verify_token

            payload = verify_token(token, "access")
            if payload:
                return self._extract_identity_from_payload(payload)
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"RS256 JWT 验证失败，尝试 HS256: {e}")

        # 回退到 HS256（向后兼容）
        try:
            import jwt

            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

            # 检查过期
            if "exp" in payload and payload["exp"] < time.time():
                logger.warning("JWT Token 已过期")
                return None

            return self._extract_identity_from_payload(payload)

        except ImportError:
            logger.warning("PyJWT 未安装，无法解析 JWT Token")
            return None
        except jwt.ExpiredSignatureError:
            logger.warning("JWT Token 已过期")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"JWT Token 无效: {e}")
            return None

    def _extract_identity_from_payload(self, payload: dict) -> dict:
        """从 JWT payload 提取身份信息"""
        user_id = payload.get("user_id") or payload.get("sub", "unknown")

        role_mask = payload.get("role_mask")
        dept_mask = payload.get("dept_mask")

        if role_mask is None and "roles" in payload:
            role_mask = self._encode_roles(payload["roles"])
        if dept_mask is None and "depts" in payload:
            dept_mask = self._encode_depts(payload["depts"])

        return {
            "user_id": user_id,
            "user_role_mask": role_mask if role_mask is not None else 0,
            "user_dept_mask": dept_mask if dept_mask is not None else 0,
        }

    def generate_token(
        self,
        user_id: str,
        roles: list[str] = None,
        depts: list[str] = None,
        role_mask: int = None,
        dept_mask: int = None,
    ) -> str:
        """
        生成 JWT Token（用于测试和认证服务对接）

        Args:
            user_id: 用户 ID
            roles: 角色列表，如 ["rd", "quality"]
            depts: 部门列表，如 ["rd_dept"]
            role_mask: 直接指定角色位掩码（优先于 roles）
            dept_mask: 直接指定部门位掩码（优先于 depts）

        Returns:
            JWT token string
        """
        try:
            import jwt

            now = int(time.time())
            payload = {
                "sub": user_id,
                "user_id": user_id,
                "iat": now,
                "exp": now + JWT_EXPIRY_HOURS * 3600,
            }

            if role_mask is not None:
                payload["role_mask"] = role_mask
            elif roles:
                payload["role_mask"] = self._encode_roles(roles)
                payload["roles"] = roles

            if dept_mask is not None:
                payload["dept_mask"] = dept_mask
            elif depts:
                payload["dept_mask"] = self._encode_depts(depts)
                payload["depts"] = depts

            token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
            return token

        except ImportError as _exc_ruf:
            raise RuntimeError("PyJWT 未安装，无法生成 JWT Token") from _exc_ruf

    def _encode_roles(self, roles: list[str]) -> int:
        """角色列表编码为位掩码"""
        mask = 0
        for role in roles:
            if role in self.roles:
                mask |= self.roles[role]
        return mask

    def _encode_depts(self, depts: list[str]) -> int:
        """部门列表编码为位掩码"""
        mask = 0
        for dept in depts:
            if dept in self.departments:
                mask |= self.departments[dept]
        return mask

    def get_demo_identity(self, role: str = "admin") -> dict:
        """获取演示身份（开发/测试用）"""
        return {
            "user_id": f"demo_{role}",
            "user_role_mask": self.roles.get(role, 0),
            "user_dept_mask": 0,
        }
