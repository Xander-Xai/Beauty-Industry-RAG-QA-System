"""User store -- SQLite-backed user management with RBAC roles."""
import hashlib
import logging
import os
import secrets
import sqlite3
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# bcrypt 依赖 — 生产模式下必须可用
try:
    import bcrypt
    _HAS_BCRYPT = True
except ImportError:
    _HAS_BCRYPT = False
    try:
        from common.config import is_production_mode
        if is_production_mode():
            raise RuntimeError("生产模式下 bcrypt 为硬依赖，请安装: pip install bcrypt")
    except ImportError:
        pass
    logger.warning("bcrypt 未安装，密码哈希使用 SHA-256（不推荐用于生产）")

# 从 config.json 读取 RBAC 角色和部门定义（与系统其他部分保持一致）
try:
    from common.config import get_config
    _cfg = get_config()
    ROLES = dict(_cfg.rbac.roles)
    DEPARTMENTS = dict(_cfg.rbac.departments)
except Exception:
    ROLES = {"admin": 2147483647}
    DEPARTMENTS = {"all": 0}


@dataclass
class User:
    user_id: str
    username: str
    display_name: str
    role_mask: int
    dept_mask: int
    is_active: bool = True
    roles: list[str] = None
    departments: list[str] = None

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "role_mask": self.role_mask,
            "dept_mask": self.dept_mask,
            "is_active": self.is_active,
            "roles": self.roles or [],
            "departments": self.departments or [],
        }


class UserStore:
    """SQLite-backed user store for RBAC."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.environ.get("DATABASE_URL", "sqlite:///./data/users.db")
            # Strip sqlite:/// prefix
            if db_path.startswith("sqlite:///"):
                db_path = db_path[len("sqlite:///"):]
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    role_mask INTEGER NOT NULL DEFAULT 0,
                    dept_mask INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_roles (
                    user_id TEXT NOT NULL,
                    role_name TEXT NOT NULL,
                    PRIMARY KEY (user_id, role_name),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_depts (
                    user_id TEXT NOT NULL,
                    dept_name TEXT NOT NULL,
                    PRIMARY KEY (user_id, dept_name),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            conn.commit()

    def _hash_password(self, password: str, salt: str = None) -> str:
        """Hash password: bcrypt (preferred) or SHA-256 fallback.

        Returns:
            bcrypt: "$2b$..." format (no salt prefix)
            sha256: "salt:hash" format (legacy, for migration compatibility)
        """
        if _HAS_BCRYPT:
            if salt is not None:
                # 验证模式：使用传入的 salt 重新计算（仅用于旧格式兼容）
                h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
                return f"{salt}:{h}"
            return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        # bcrypt 不可用：降级到 SHA-256
        if salt is None:
            salt = secrets.token_hex(16)
        h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
        return f"{salt}:{h}"

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        """Verify password against stored hash (supports bcrypt + SHA-256 migration)."""
        if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
            # bcrypt 格式
            if _HAS_BCRYPT:
                return bcrypt.checkpw(password.encode(), stored_hash.encode())
            logger.error("存储的密码为 bcrypt 格式但 bcrypt 未安装")
            return False
        # 旧版 SHA-256 格式: "salt:hash"
        try:
            salt, _ = stored_hash.split(":", 1)
        except ValueError:
            return False
        return self._hash_password(password, salt) == stored_hash

    def create_user(self, user_id: str, username: str, password: str,
                    display_name: str, role_names: list[str] = None,
                    dept_names: list[str] = None) -> User:
        """Create a new user."""
        role_mask = 0
        for r in (role_names or []):
            role_mask |= ROLES.get(r, 0)
        dept_mask = 0
        for d in (dept_names or []):
            dept_mask |= DEPARTMENTS.get(d, 0)

        password_hash = self._hash_password(password)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO users (user_id, username, password_hash, display_name, role_mask, dept_mask) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, password_hash, display_name, role_mask, dept_mask),
            )
            for r in (role_names or []):
                conn.execute("INSERT INTO user_roles (user_id, role_name) VALUES (?, ?)", (user_id, r))
            for d in (dept_names or []):
                conn.execute("INSERT INTO user_depts (user_id, dept_name) VALUES (?, ?)", (user_id, d))
            conn.commit()

        return User(user_id=user_id, username=username, display_name=display_name,
                    role_mask=role_mask, dept_mask=dept_mask,
                    roles=role_names or [], departments=dept_names or [])

    def authenticate(self, username: str, password: str) -> User | None:
        """Authenticate user by username/password. Returns User or None."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
            ).fetchone()
            if not row:
                # H-5: 不存在用户也执行 dummy bcrypt，防止时序侧信道枚举
                if _HAS_BCRYPT:
                    bcrypt.hashpw(b"dummy_password", bcrypt.gensalt(rounds=4))
                return None
            if not self._verify_password(password, row["password_hash"]):
                return None
            roles = [r[0] for r in conn.execute("SELECT role_name FROM user_roles WHERE user_id = ?", (row["user_id"],)).fetchall()]
            depts = [d[0] for d in conn.execute("SELECT dept_name FROM user_depts WHERE user_id = ?", (row["user_id"],)).fetchall()]
            return User(
                user_id=row["user_id"], username=row["username"],
                display_name=row["display_name"], role_mask=row["role_mask"],
                dept_mask=row["dept_mask"], is_active=bool(row["is_active"]),
                roles=roles, departments=depts,
            )

    def get_user(self, user_id: str) -> User | None:
        """Get user by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if not row:
                return None
            roles = [r[0] for r in conn.execute("SELECT role_name FROM user_roles WHERE user_id = ?", (user_id,)).fetchall()]
            depts = [d[0] for d in conn.execute("SELECT dept_name FROM user_depts WHERE user_id = ?", (user_id,)).fetchall()]
            return User(
                user_id=row["user_id"], username=row["username"],
                display_name=row["display_name"], role_mask=row["role_mask"],
                dept_mask=row["dept_mask"], is_active=bool(row["is_active"]),
                roles=roles, departments=depts,
            )

    def list_users(self) -> list[User]:
        """List all users."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT user_id FROM users ORDER BY created_at").fetchall()
            return [self.get_user(r["user_id"]) for r in rows]

    def update_user_roles(self, user_id: str, role_names: list[str], dept_names: list[str]) -> User | None:
        """Update user's roles and departments."""
        role_mask = 0
        for r in role_names:
            role_mask |= ROLES.get(r, 0)
        dept_mask = 0
        for d in dept_names:
            dept_mask |= DEPARTMENTS.get(d, 0)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE users SET role_mask = ?, dept_mask = ? WHERE user_id = ?",
                        (role_mask, dept_mask, user_id))
            conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM user_depts WHERE user_id = ?", (user_id,))
            for r in role_names:
                conn.execute("INSERT INTO user_roles (user_id, role_name) VALUES (?, ?)", (user_id, r))
            for d in dept_names:
                conn.execute("INSERT INTO user_depts (user_id, dept_name) VALUES (?, ?)", (user_id, d))
            conn.commit()
        return self.get_user(user_id)
