"""
离线管线入口脚本

用法：
    python run_offline.py --mode incremental    # 增量更新
    python run_offline.py --mode full           # 全量重建
    python run_offline.py --mode create-index   # 创建 Milvus Collection
    python run_offline.py --mode feedback       # 离线反馈闭环（日志采样 + 参数更新）
    python run_offline.py --mode rewrite-feedback  # Query Rewrite 反馈闭环
"""

import argparse
import json
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="离线知识库构建管线")
    parser.add_argument("--mode", choices=[
        "incremental", "full", "create-index", "feedback", "rewrite-feedback",
    ], default="incremental", help="运行模式")
    parser.add_argument("--data-dir", default=None, help="数据目录（覆盖 config）")
    parser.add_argument("--force-full", action="store_true", help="强制全量更新（增量模式下忽略增量状态）")
    args = parser.parse_args()

    with open("config.json", encoding="utf-8") as f:
        config = json.load(f)

    if args.data_dir:
        config["knowledge_base"]["data_dir"] = args.data_dir

    if args.mode == "create-index":
        from offline.vectorizer import Vectorizer
        vectorizer = Vectorizer()
        vectorizer.create_milvus_collections()
        logger.info("Milvus Collection 创建完成")

    elif args.mode == "incremental":
        from offline.scheduler import OfflineScheduler
        scheduler = OfflineScheduler()
        result = scheduler.run_incremental_update(force=args.force_full)
        logger.info(f"增量更新结果: {json.dumps(result, ensure_ascii=False)}")

    elif args.mode == "full":
        from offline.scheduler import OfflineScheduler
        scheduler = OfflineScheduler()
        result = scheduler.run_full_rebuild()
        logger.info(f"全量重建结果: {json.dumps(result, ensure_ascii=False)}")

    elif args.mode == "feedback":
        from offline.feedback_loop import FeedbackLoop
        loop = FeedbackLoop()
        result = loop.run_full_feedback_cycle()
        logger.info(f"反馈闭环结果: {json.dumps(result, ensure_ascii=False)}")

    elif args.mode == "rewrite-feedback":
        from rewrite.feedback import RewriteFeedback
        fb = RewriteFeedback()
        result = fb.run_feedback_cycle()
        logger.info(f"Rewrite 反馈闭环结果: {json.dumps(result, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
