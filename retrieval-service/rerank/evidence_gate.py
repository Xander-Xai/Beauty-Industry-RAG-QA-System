"""Evidence Gate — re-export of the canonical implementation.

This module used to carry a second, independently maintained copy of
``EvidenceEnsembleGate``. That copy had drifted behind ``retrieval/evidence_gate.py``:
it had no fail-closed ``gate_mode`` marker, it ignored ``weights_override``,
``thresholds_override`` and ``conservative_mode`` entirely, and its inter-document
consistency check returned a fixed ``0.8`` when the NLI call raised — an
infrastructure failure that *raised* the evidence score instead of lowering it.

Because ``retrieval-service`` is a deployed microservice (``docker-compose.microservices.yml``,
port 8200, reached by the API gateway), the weaker copy was the one actually gating
requests on that path. Rather than maintain two gates, this module now re-exports the
canonical one, so both deployments share one implementation and one set of weights and
thresholds.

The import path is preserved deliberately: ``retrieval-service/main.py`` imports this
module, and so do the architecture-contract tests.
"""

from retrieval.evidence_gate import EvidenceEnsembleGate

__all__ = ["EvidenceEnsembleGate"]
