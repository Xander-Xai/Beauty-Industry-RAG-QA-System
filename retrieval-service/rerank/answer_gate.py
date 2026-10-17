"""Answer Gate — re-export of the canonical implementation.

This module used to carry a second copy of ``AnswerGate``. It had drifted behind
``retrieval/answer_gate.py``, so the deployed ``retrieval-service`` ran a different NLI
verification path than the online pipeline: the canonical module carries the
``RerankBatchAggregator`` micro-batching hook that keeps GPU utilisation up, which this
copy never had.

``retrieval-service`` is a deployed microservice, so the divergence was not theoretical.
Both deployments now share one implementation. The import path is preserved because
``retrieval-service/main.py`` imports this module.
"""

from retrieval.answer_gate import AnswerGate

__all__ = ["AnswerGate"]
