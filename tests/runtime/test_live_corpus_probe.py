"""Live corpus-probe check against the real Elasticsearch / Qdrant services.

Skipped unless ``RUN_RUNTIME_VALIDATION=1``. When it does run it records what it
actually observed — it does not assert a particular corpus exists, because the
point is to show the probe measures reality rather than returning a constant.

Run with::

    RUN_RUNTIME_VALIDATION=1 python3 -m pytest tests/runtime/test_live_corpus_probe.py -v
"""

from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.runtime

from benchmarks.corpus import distinct_passages, inspect_correspondence  # noqa: E402
from benchmarks.dataset import load_queries  # noqa: E402

COMMITTED = "tests/evaluation/golden_set.jsonl"


@pytest.mark.skipif(
    os.environ.get("RUN_RUNTIME_VALIDATION") not in ("1", "true", "yes"),
    reason="set RUN_RUNTIME_VALIDATION=1 to probe the live corpus",
)
def test_the_real_probe_measures_the_live_corpus():
    queries = load_queries(COMMITTED)
    passages = distinct_passages(queries)
    inspection = inspect_correspondence(passages, total_passages=len(queries))

    payload = inspection.as_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    # The probe must have tried the real stores and reported each one.
    systems = {fingerprint["system"] for fingerprint in payload["fingerprints"]}
    assert {"elasticsearch", "qdrant"} <= systems

    # Whichever way the verdict lands, it must be derived from the measurement:
    # matched <= checked <= distinct, and a ratio consistent with the counts.
    correspondence = payload["correspondence"]
    assert correspondence["passages_matched"] <= correspondence["passages_checked"]
    assert correspondence["passages_checked"] <= correspondence["passages_distinct"]
    if correspondence["passages_checked"]:
        expected = correspondence["passages_matched"] / correspondence["passages_checked"]
        assert correspondence["ratio"] == pytest.approx(expected)

    # A reachable store must carry a real content fingerprint, and an unreachable
    # one must say why rather than look like an empty corpus. Which of the two
    # applies depends on this host, so neither is asserted as required.
    for fingerprint in payload["fingerprints"]:
        if fingerprint["reachable"]:
            assert fingerprint["content_sha256"] is not None
            assert len(fingerprint["content_sha256"]) == 64
        else:
            assert fingerprint["content_sha256"] is None
            assert fingerprint["error"]

    # The verdict must never claim a usable corpus without evidence for it.
    if correspondence["ratio"] < 1.0:
        assert payload["verdict"] != "full"
