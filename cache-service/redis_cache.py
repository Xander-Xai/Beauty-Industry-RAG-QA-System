"""
RedisCache -- 微服务包装器

引用共享模块 cache.redis_cache.RedisCache，
消除与 cache/redis_cache.py 的代码重复（GAP-14）。
"""

import os
import sys

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from cache.redis_cache import RedisCache  # noqa: F401

__all__ = ["RedisCache"]
