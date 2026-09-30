"""Planned Airflow DAG contract for offline ingestion (PRD §3.7).

调度策略：
- 每周日 02:00 执行增量更新（weekly_incremental）
- 每月 1 日 03:00 执行全量重建（monthly_full_rebuild）
- 每次构建完成后自动版本滚动 + 过期文档归档

The production ingestion modules referenced below are not currently included.
No active DAG is registered until those modules are available. Follow-up scope:
https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/2
"""

from __future__ import annotations

import json
import os
import sys
from importlib.util import find_spec

# ── Airflow 导入（不可用时跳过，仅供人工参考） ──────────────────────
try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator
    from airflow.utils.dates import days_ago

    AIRFLOW_AVAILABLE = True
except ImportError:
    AIRFLOW_AVAILABLE = False

INGESTION_AVAILABLE = all(
    find_spec(module) is not None
    for module in (
        "offline.document_processor",
        "offline.image_processor",
        "offline.vectorizer",
        "offline.scheduler",
        "offline.feedback_loop",
    )
)

# ── 项目根目录 ─────────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ── 默认参数 ───────────────────────────────────────────────────────────
DEFAULT_ARGS = {
    "owner": "rag-admin",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay_minutes": 30,
    "execution_timeout_minutes": 120,
}


# ── 任务函数 ───────────────────────────────────────────────────────────
def _task_incremental_update():
    """每周增量更新"""
    from offline.scheduler import OfflineScheduler

    scheduler = OfflineScheduler()
    result = scheduler.run_incremental_update()
    print(f"增量更新完成: {json.dumps(result, ensure_ascii=False)}")
    return result


def _task_full_rebuild():
    """每月全量重建"""
    from offline.scheduler import OfflineScheduler

    scheduler = OfflineScheduler()
    result = scheduler.run_full_rebuild()
    print(f"全量重建完成: {json.dumps(result, ensure_ascii=False)}")
    return result


def _task_bump_version_epoch():
    """版本滚动"""
    from offline.scheduler import OfflineScheduler

    scheduler = OfflineScheduler()
    new_epoch = scheduler.bump_version_epoch()
    print(f"版本滚动完成: {new_epoch}")
    return new_epoch


def _task_archive_expired(current_epoch: str = None):
    """过期文档归档"""
    from offline.scheduler import OfflineScheduler

    from common.config import get_config_dict

    # 从 config.json 读取当前 epoch
    cfg = get_config_dict()
    epoch = current_epoch or cfg.get("knowledge_version_epoch", "")

    scheduler = OfflineScheduler()
    scheduler.archive_expired_documents(epoch)
    print(f"过期文档归档完成: epoch={epoch}")


def _task_feedback_loop():
    """离线反馈闭环 — 参数更新"""
    from offline.feedback_loop import FeedbackLoop

    loop = FeedbackLoop()
    result = loop.run_full_feedback_cycle()
    print(f"反馈闭环完成: {json.dumps(result, ensure_ascii=False)}")
    return result


# ── Missing dependencies: do not register unusable DAGs ───────────────
if not AIRFLOW_AVAILABLE or not INGESTION_AVAILABLE:
    print("Offline ingestion modules are not available; no active Airflow DAGs are registered.")


# ── DAG 定义（仅在 Airflow 可用时生效） ────────────────────────────────
if AIRFLOW_AVAILABLE and INGESTION_AVAILABLE:
    # DAG 1: 每周增量更新
    weekly_incremental_dag = DAG(
        dag_id="weekly_knowledge_incremental",
        default_args=DEFAULT_ARGS,
        description="每周增量更新：扫描新文档 → 向量化 → 版本滚动 → 归档过期",
        schedule_interval="0 2 * * 0",  # 每周日 02:00
        start_date=days_ago(1),
        catchup=False,
        tags=["knowledge-base", "weekly"],
    )

    with weekly_incremental_dag:
        t_incremental = PythonOperator(
            task_id="incremental_update",
            python_callable=_task_incremental_update,
        )
        t_bump_epoch = PythonOperator(
            task_id="bump_version_epoch",
            python_callable=_task_bump_version_epoch,
        )
        t_archive = PythonOperator(
            task_id="archive_expired",
            python_callable=_task_archive_expired,
        )
        t_incremental >> t_bump_epoch >> t_archive

    # DAG 2: 每月全量重建
    monthly_full_dag = DAG(
        dag_id="monthly_knowledge_full_rebuild",
        default_args=DEFAULT_ARGS,
        description="每月全量重建：清空 Collection → 全量处理 → 版本滚动 → 归档",
        schedule_interval="0 3 1 * *",  # 每月 1 日 03:00
        start_date=days_ago(1),
        catchup=False,
        tags=["knowledge-base", "monthly"],
    )

    with monthly_full_dag:
        t_full_rebuild = PythonOperator(
            task_id="full_rebuild",
            python_callable=_task_full_rebuild,
        )
        t_bump_epoch_m = PythonOperator(
            task_id="bump_version_epoch",
            python_callable=_task_bump_version_epoch,
        )
        t_archive_m = PythonOperator(
            task_id="archive_expired",
            python_callable=_task_archive_expired,
        )
        t_full_rebuild >> t_bump_epoch_m >> t_archive_m

    # DAG 3: 每周反馈闭环
    feedback_dag = DAG(
        dag_id="weekly_feedback_loop",
        default_args=DEFAULT_ARGS,
        description="每周反馈闭环：日志采样 → 参数更新（RRF/Evidence Gate）",
        schedule_interval="0 4 * * 1",  # 每周一 04:00
        start_date=days_ago(1),
        catchup=False,
        tags=["feedback", "weekly"],
    )

    with feedback_dag:
        t_feedback = PythonOperator(
            task_id="feedback_loop",
            python_callable=_task_feedback_loop,
        )
