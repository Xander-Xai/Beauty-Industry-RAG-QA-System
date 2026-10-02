"""``python -m benchmarks`` entry point (same CLI as ``-m benchmarks.retrieval_benchmark``)."""

from benchmarks.retrieval_benchmark import main

if __name__ == "__main__":
    raise SystemExit(main())
