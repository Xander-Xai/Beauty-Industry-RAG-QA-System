"""
离线管线入口脚本

用法：
    python3 run_offline.py --mode incremental      # 增量更新
    python3 run_offline.py --mode full             # 全量重建
    python3 run_offline.py --mode create-index     # 创建 Qdrant Collection
    python3 run_offline.py --mode feedback         # 离线反馈闭环（日志采样 + 参数更新）
    python3 run_offline.py --mode rewrite-feedback # Query Rewrite 反馈闭环
"""

import argparse
import logging
import sys

from common.config import get_config_dict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="离线知识库工具")
    parser.add_argument(
        "--mode",
        choices=[
            "incremental",
            "full",
            "create-index",
            "feedback",
            "rewrite-feedback",
        ],
        default="incremental",
        help="运行模式",
    )
    parser.add_argument("--data-dir", default=None, help="数据目录（覆盖 config）")
    parser.add_argument("--force-full", action="store_true", help="强制全量更新（增量模式下忽略增量状态）")
    args = parser.parse_args()

    if args.mode == "rewrite-feedback":
        get_config_dict()
        from rewrite.feedback import RewriteFeedback

        fb = RewriteFeedback()
        fb.run_feedback_cycle()
        logger.info("Rewrite feedback cycle completed")
        return 0

    print(
        "Offline ingestion pipeline is not currently included in this repository.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
