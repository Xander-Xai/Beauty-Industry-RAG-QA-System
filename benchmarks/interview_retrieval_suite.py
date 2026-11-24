"""Strict five-arm real retrieval acceptance; never substitutes fixture results."""

from __future__ import annotations

import argparse

from benchmarks import retrieval_benchmark

ARMS = (
    "bm25",
    "dense",
    "hybrid_rrf",
    "hybrid_rrf_biencoder",
    "hybrid_rrf_biencoder_crossencoder",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="Reviewed, attributable golden set JSONL")
    parser.add_argument("--output-dir", required=True, help="New experiment artifact root")
    args = parser.parse_args(argv)
    return retrieval_benchmark.main(
        [
            "--dataset", args.dataset,
            "--output-dir", args.output_dir,
            "--configs", ",".join(ARMS),
            "--executor", "real",
            "--top-k", "10",
            "--require-contract",
            "--require-integrity",
            "--require-results",
            "--require-source-approval",
            "--require-complete-suite",
            "--require-publishable",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
