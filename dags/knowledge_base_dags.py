"""Airflow DAG definitions for the offline ingestion pipeline.

Scheduling cadence is read from ``config.json`` (``offline.scheduler``) rather
than hardcoded. Airflow is optional: when it is not installed, or the offline
modules are unavailable, no DAG is registered and importing this module still
succeeds. The scheduler builds, validates and optionally seals epochs; it never
switches the active ``knowledge_version_epoch`` automatically.
"""

from __future__ import annotations

import json
import os
import sys
from importlib.util import find_spec

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator
    from airflow.utils.dates import days_ago

    AIRFLOW_AVAILABLE = True
except ImportError:
    AIRFLOW_AVAILABLE = False


def _ingestion_modules_available() -> bool:
    """Return whether all offline ingestion modules can be discovered."""
    try:
        if find_spec("offline") is None:
            return False
        return all(
            find_spec(module) is not None
            for module in (
                "offline.document_processor",
                "offline.image_processor",
                "offline.vectorizer",
                "offline.scheduler",
                "offline.feedback_loop",
            )
        )
    except ModuleNotFoundError as exc:
        if exc.name == "offline" or (exc.name and exc.name.startswith("offline.")):
            return False
        raise


INGESTION_AVAILABLE = _ingestion_modules_available()


def _scheduler_config():
    from common.config import get_config_dict
    from offline.scheduler import load_scheduler_config

    return load_scheduler_config(get_config_dict())


DEFAULT_ARGS = {
    "owner": "rag-admin",
    "depends_on_past": False,
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay_minutes": 30,
    "execution_timeout_minutes": 120,
}


def _task_incremental_update():
    from offline.scheduler import OfflineScheduler

    result = OfflineScheduler().run_incremental_cycle()
    print(f"incremental cycle complete: {json.dumps(result, ensure_ascii=False)}")
    return result


def _task_full_rebuild():
    from offline.scheduler import OfflineScheduler

    result = OfflineScheduler().run_full_rebuild_cycle()
    print(f"full rebuild complete: {json.dumps(result, ensure_ascii=False)}")
    return result


def _task_feedback_loop():
    from common.config import get_config_dict
    from offline.feedback_loop import FeedbackLoop

    config = get_config_dict()
    feedback_config = config.get("offline", {}).get("feedback", {})
    store_path = feedback_config.get("store_path", "data/feedback/feedback.sqlite3")
    loop = FeedbackLoop(store_path, output_dir=feedback_config.get("output_dir", "./data/feedback"))
    result = loop.run_full_feedback_cycle()
    print(f"feedback cycle complete: {json.dumps(result, ensure_ascii=False)}")
    return result


if not AIRFLOW_AVAILABLE or not INGESTION_AVAILABLE:
    print("Offline ingestion modules or Airflow are not available; no active Airflow DAGs are registered.")


if AIRFLOW_AVAILABLE and INGESTION_AVAILABLE:
    _scheduler = _scheduler_config()

    if _scheduler.incremental_enabled:
        weekly_incremental_dag = DAG(
            dag_id="weekly_knowledge_incremental",
            default_args=DEFAULT_ARGS,
            description="Weekly incremental build: discover → carry forward → process → validate → optional seal",
            schedule_interval=_scheduler.incremental_cron,
            start_date=days_ago(1),
            catchup=False,
            tags=["knowledge-base", "weekly"],
        )

        with weekly_incremental_dag:
            t_incremental = PythonOperator(
                task_id="incremental_update",
                python_callable=_task_incremental_update,
            )

    if _scheduler.full_rebuild_enabled:
        monthly_full_dag = DAG(
            dag_id="monthly_knowledge_full_rebuild",
            default_args=DEFAULT_ARGS,
            description="Monthly full rebuild into a new epoch (manual activation)",
            schedule_interval=_scheduler.full_rebuild_cron,
            start_date=days_ago(1),
            catchup=False,
            tags=["knowledge-base", "monthly"],
        )

        with monthly_full_dag:
            t_full_rebuild = PythonOperator(
                task_id="full_rebuild",
                python_callable=_task_full_rebuild,
            )

    feedback_dag = DAG(
        dag_id="weekly_feedback_loop",
        default_args=DEFAULT_ARGS,
        description="Weekly feedback collection and review-gated dataset export",
        schedule_interval="0 4 * * 1",
        start_date=days_ago(1),
        catchup=False,
        tags=["feedback", "weekly"],
    )

    with feedback_dag:
        t_feedback = PythonOperator(
            task_id="feedback_loop",
            python_callable=_task_feedback_loop,
        )
