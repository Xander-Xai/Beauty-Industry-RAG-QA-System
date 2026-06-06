"""根 conftest.py — 配置测试收集行为"""
import os

# 默认忽略 e2e_remote 测试目录（需要外部 API key，且 session-scoped patches 会泄漏）
# 运行 e2e 测试请使用: pytest tests/e2e_remote/ --no-header
_e2e_remote_dir = os.path.join(os.path.dirname(__file__), "tests", "e2e_remote")

# 通过环境变量控制是否包含 e2e 测试
if os.environ.get("RUN_E2E_TESTS", "").lower() not in ("1", "true", "yes"):
    collect_ignore = [_e2e_remote_dir]
