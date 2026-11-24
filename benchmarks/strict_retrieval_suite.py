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


ARCHITECTURE_ARMS = (
    "hybrid_rrf_crossencoder_a",
    "hybrid_rrf_crossencoder_b",
    "hybrid_rrf_crossencoder_dual",
    "hybrid_rrf_biencoder_crossencoder_a",
    "hybrid_rrf_biencoder_crossencoder_b",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="Reviewed, attributable golden set JSONL")
    parser.add_argument("--output-dir", required=True, help="New experiment artifact root")
    parser.add_argument(
        "--architecture-ablation", action="store_true", help="Include five isolated ranking challengers"
    )
    args = parser.parse_args(argv)
    arms = ARMS + ARCHITECTURE_ARMS if args.architecture_ablation else ARMS
    return retrieval_benchmark.main(
        [
            "--dataset",
            args.dataset,
            "--output-dir",
            args.output_dir,
            "--configs",
            ",".join(arms),
            "--executor",
            "real",
            "--top-k",
            "10",
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
