"""Cross-platform advisory lock adapter tests."""

from __future__ import annotations

import threading
import time

from offline.file_lock import FileLockProvider, file_lock, resolve_lock_dir


def test_file_lock_serializes_same_namespace():
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    def worker():
        nonlocal active, max_active
        with file_lock("shared-namespace"):
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with state_lock:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert max_active == 1


def test_different_namespaces_do_not_block():
    rendezvous = threading.Barrier(2)
    errors = []

    def worker(namespace):
        try:
            with file_lock(namespace):
                rendezvous.wait(timeout=1)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
    assert errors == []


def test_lock_provider_scopes_and_configured_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFLINE_INGESTION_LOCK_DIR", str(tmp_path))
    provider = FileLockProvider()
    with provider.replacement_lock("doc", "epoch"):
        assert list(tmp_path.glob("*.lock"))
    with provider.epoch_lock("epoch", shared=True):
        assert resolve_lock_dir() == tmp_path
