"""Regression tests for Airflow DAG loading without the ingestion pipeline."""

import importlib
import subprocess
import sys
import textwrap
from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_dag_import_succeeds_when_repository_root_is_not_on_sys_path(tmp_path):
    script = textwrap.dedent(
        """
        import importlib
        import sys
        from pathlib import Path

        project_root = Path(sys.argv[1]).resolve()
        sys.path[:] = [
            entry for entry in sys.path
            if entry and Path(entry).resolve() != project_root
        ]
        sys.path.insert(0, str(project_root))
        import dags
        sys.path.remove(str(project_root))

        assert str(project_root) not in sys.path
        module = importlib.import_module("dags.knowledge_base_dags")

        assert str(project_root) in sys.path
        # The offline ingestion modules now exist, so the probe succeeds.
        assert module.INGESTION_AVAILABLE is True
        # Airflow is not installed, so no DAG objects are registered.
        assert not any(
            name in vars(module)
            for name in ("weekly_incremental_dag", "monthly_full_dag", "feedback_dag")
        )
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(PROJECT_ROOT)],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_ingestion_probe_detects_all_modules_after_root_path_setup(monkeypatch):
    dags = importlib.import_module("dags.knowledge_base_dags")
    probed_modules = []

    def discover_module(name):
        assert str(PROJECT_ROOT) in sys.path
        probed_modules.append(name)
        return ModuleSpec(name, loader=None)

    monkeypatch.setattr(dags, "find_spec", discover_module)

    assert dags._ingestion_modules_available() is True
    assert probed_modules == [
        "offline",
        "offline.document_processor",
        "offline.image_processor",
        "offline.vectorizer",
        "offline.scheduler",
        "offline.feedback_loop",
    ]


def test_ingestion_probe_returns_false_for_missing_parent(monkeypatch):
    dags = importlib.import_module("dags.knowledge_base_dags")

    def missing_parent(name):
        assert name == "offline"
        raise ModuleNotFoundError("No module named 'offline'", name="offline")

    monkeypatch.setattr(dags, "find_spec", missing_parent)

    assert dags._ingestion_modules_available() is False


def test_ingestion_probe_does_not_swallow_unrelated_import_errors(monkeypatch):
    dags = importlib.import_module("dags.knowledge_base_dags")

    def missing_dependency(name):
        raise ModuleNotFoundError("No module named 'unexpected_dependency'", name="unexpected_dependency")

    monkeypatch.setattr(dags, "find_spec", missing_dependency)

    with pytest.raises(ModuleNotFoundError, match="unexpected_dependency"):
        dags._ingestion_modules_available()
