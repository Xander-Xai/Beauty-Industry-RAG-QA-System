from benchmarks import strict_retrieval_suite as suite


def test_strict_suite_cannot_silently_publish_partial_results(monkeypatch):
    captured = []

    def blocked(args):
        captured.extend(args)
        return 2

    monkeypatch.setattr(suite.retrieval_benchmark, "main", blocked)
    assert suite.main(["--dataset", "reviewed.jsonl", "--output-dir", "new-run"]) == 2
    assert captured[captured.index("--configs") + 1].split(",") == list(suite.ARMS)
    assert len(suite.ARMS) == 5
    assert captured[captured.index("--executor") + 1] == "real"
    assert "--require-complete-suite" in captured
    assert "--require-publishable" in captured
    assert "--require-source-approval" in captured
    assert "--allow-dirty" not in captured
    assert "--limit" not in captured
