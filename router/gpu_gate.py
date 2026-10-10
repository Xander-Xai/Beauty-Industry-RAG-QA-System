"""Acceptance gate for the configured Qwen3-4B / 14B vLLM deployment.

Why this exists
---------------
`config.json` describes a two-GPU topology: ``gpu0`` serves ``Qwen3-14B`` on
port 8100 and ``gpu1`` serves ``Qwen3-4B`` on port 8101, with
``model_routing.tiers.complex → gen_14b``. Every one of those is a *declaration*.
A configuration file is evidence that a deployment was planned, not that it
runs, and the distinction matters here for two reasons:

1. ``deployment_mode`` is ``development`` in this repository, so
   ``models/llm_client.py:220-234`` downgrades the complex tier to ``simple``
   and **the 14B endpoint is never called**. Every request goes to 4B. A reader
   looking at the routing contract alone could conclude both tiers are live.
2. A single-GPU host cannot exercise a two-GPU topology. Serving a 14B model on
   one card is a *different deployment* from the configured one, and its latency
   and throughput numbers say nothing about the intended topology.

So this gate separates four questions that the config file blends together:

* are the configured endpoints actually answering?
* are the configured weights present?
* does the host have the GPUs the topology assumes?
* has anyone actually measured throughput on this deployment?

The last one is the reason this module never prints a throughput or latency
number of its own. ``gpu_metrics_measured`` is ``False`` unless a real load run
recorded samples, and this module does not run one. A fabricated GPU QPS is the
single most tempting and least verifiable number in this project, so it is
structurally absent: there is no code path here that can produce one.

Usage::

    python3 -m router.gpu_gate

Exit codes: ``0`` satisfied · ``3`` pending (an absent asset) · ``1`` failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROBE_TIMEOUT_SECONDS = 2.0

STATUS_SATISFIED = "SATISFIED"
STATUS_PENDING = "PENDING"
STATUS_FAILED = "FAILED"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PENDING = 3

#: Endpoint key -> (config path to its model dir, port config key).
ENDPOINT_SPECS: tuple[tuple[str, tuple[str, str], str], ...] = (
    ("gen_14b", ("gpu0", "gen_14b"), "Qwen3-14B"),
    ("gen_4b", ("gpu1", "vllm_4b"), "Qwen3-4B"),
)


@dataclass
class EndpointStatus:
    endpoint: str
    label: str
    url: str
    reachable: bool
    http_status: int | None
    model_path: str | None
    weights_present: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GpuTopologyStatus:
    """What the host actually has, versus what the configuration assumes."""

    assumed_gpu_count: int
    observed_gpu_count: int
    observed: list[dict[str, Any]]
    torch_available: bool
    cuda_available: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GpuGateReport:
    status: str
    reasons: list[str] = field(default_factory=list)
    endpoints: list[EndpointStatus] = field(default_factory=list)
    topology: GpuTopologyStatus | None = None
    routing: dict[str, Any] = field(default_factory=dict)
    #: Always False here. This module measures presence, never throughput.
    gpu_metrics_measured: bool = False
    environment: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def exit_code(self) -> int:
        if self.status == STATUS_SATISFIED:
            return EXIT_OK
        return EXIT_PENDING if self.status == STATUS_PENDING else EXIT_FAILED


def _config() -> dict:
    from common.config import get_config_dict

    return get_config_dict() or {}


def _endpoint_url(config: dict, gpu_key: str, model_key: str) -> str:
    model = ((config.get(gpu_key, {}) or {}).get("models", {}) or {}).get(model_key, {}) or {}
    port = model.get("port")
    env_name = "VLLM_4B_URL" if model_key == "vllm_4b" else "VLLM_GEN_14B_URL"
    return os.environ.get(env_name) or (f"http://localhost:{port}" if port else "")


def _probe_endpoint(url: str) -> tuple[bool, int | None, str]:
    """Ask vLLM what it serves. Read-only, short timeout, never raises."""
    if not url:
        return False, None, "no URL configured"
    target = f"{url.rstrip('/')}/v1/models"
    try:
        with urllib.request.urlopen(target, timeout=PROBE_TIMEOUT_SECONDS) as response:  # noqa: S310
            return True, response.status, "served /v1/models"
    except urllib.error.HTTPError as exc:
        # A 404/503 from a live server still proves the port is serving.
        return True, exc.code, f"endpoint answered HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, None, f"unreachable: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return False, None, f"{type(exc).__name__}: {exc}"


def _resolve_weights(model_path: str | None) -> tuple[bool, str | None]:
    if not model_path:
        return False, "no model_path configured"
    path = Path(model_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        return False, f"weights absent: {model_path}"
    return True, str(path)


def gpu_topology_status(config: dict | None = None) -> GpuTopologyStatus:
    """Compare the assumed two-GPU topology against the host actually present."""
    cfg = config if config is not None else _config()
    assumed = 0
    for gpu_key in ("gpu0", "gpu1"):
        if (cfg.get(gpu_key) or {}).get("models"):
            assumed += 1

    observed: list[dict[str, Any]] = []
    torch_available = False
    cuda_available = False
    try:
        import torch

        torch_available = True
        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            for index in range(torch.cuda.device_count()):
                properties = torch.cuda.get_device_properties(index)
                observed.append(
                    {
                        "index": index,
                        "name": properties.name,
                        "total_memory_bytes": int(properties.total_memory),
                    }
                )
    except Exception as exc:  # noqa: BLE001
        detail = f"torch unavailable ({type(exc).__name__}: {exc})"
        return GpuTopologyStatus(
            assumed_gpu_count=assumed,
            observed_gpu_count=0,
            observed=[],
            torch_available=False,
            cuda_available=False,
            detail=detail,
        )

    if not cuda_available:
        detail = "CUDA is not visible; the configured GPU topology cannot be exercised"
    elif len(observed) < assumed:
        detail = (
            f"config declares {assumed} GPU roles but {len(observed)} device(s) are visible; "
            "a two-GPU topology cannot be validated here"
        )
    else:
        detail = f"{len(observed)} GPU(s) visible, matching the configured topology"

    return GpuTopologyStatus(
        assumed_gpu_count=assumed,
        observed_gpu_count=len(observed),
        observed=observed,
        torch_available=torch_available,
        cuda_available=cuda_available,
        detail=detail,
    )


def gpu_gate_status(config: dict | None = None) -> GpuGateReport:
    """Probe every configured endpoint and the GPU topology. Read-only."""
    cfg = config if config is not None else _config()
    reasons: list[str] = []
    endpoints: list[EndpointStatus] = []

    for key, (gpu_key, model_key), label in ENDPOINT_SPECS:
        model_cfg = ((cfg.get(gpu_key, {}) or {}).get("models", {}) or {}).get(model_key, {}) or {}
        url = _endpoint_url(cfg, gpu_key, model_key)
        reachable, http_status, detail = _probe_endpoint(url)
        weights_present, weight_detail = _resolve_weights(model_cfg.get("model_path"))

        if not reachable:
            reasons.append(f"{label} endpoint unreachable at {url or '(unconfigured)'}: {detail}")
        if not weights_present:
            reasons.append(f"{label} weights: {weight_detail}")

        endpoints.append(
            EndpointStatus(
                endpoint=key,
                label=label,
                url=url,
                reachable=reachable,
                http_status=http_status,
                model_path=model_cfg.get("model_path"),
                weights_present=weights_present,
                detail=detail,
            )
        )

    topology = gpu_topology_status(cfg)
    if not topology.cuda_available or topology.observed_gpu_count < topology.assumed_gpu_count:
        reasons.append(topology.detail)

    # The routing contract itself: which tier actually resolves, given the mode.
    routing_cfg = cfg.get("model_routing", {}) or {}
    tiers = routing_cfg.get("tiers", {}) or {}
    complex_endpoint = (tiers.get("complex", {}) or {}).get("endpoint")
    simple_endpoint = (tiers.get("simple", {}) or {}).get("endpoint")
    deployment_mode = cfg.get("deployment_mode")
    try:
        from common.config import is_production_mode

        production = bool(is_production_mode())
    except Exception:  # noqa: BLE001
        production = False

    routing = {
        "deployment_mode": deployment_mode,
        "is_production_mode": production,
        "configured_complex_endpoint": complex_endpoint,
        "configured_simple_endpoint": simple_endpoint,
        "complex_tier_reachable_at_runtime": bool(
            production and complex_endpoint and any(e.endpoint == complex_endpoint and e.reachable for e in endpoints)
        ),
        "note": (
            "In non-production mode llm_client downgrades the complex tier to simple, so the "
            "14B endpoint is not called even when it is configured."
            if not production
            else "Production mode: the complex tier resolves to the 14B endpoint."
        ),
    }
    if not production:
        reasons.append(
            f"deployment_mode={deployment_mode!r}: the complex tier is downgraded to simple at "
            "runtime, so the 14B path is not exercised"
        )

    status = STATUS_SATISFIED if not reasons else STATUS_PENDING
    return GpuGateReport(
        status=status,
        reasons=reasons,
        endpoints=endpoints,
        topology=topology,
        routing=routing,
        environment={
            "python": sys.version.split()[0],
            "vllm_installed": _module_present("vllm"),
        },
    )


def _module_present(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except Exception:  # noqa: BLE001
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acceptance gate for the configured vLLM GPU topology.")
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument(
        "--require-live",
        action="store_true",
        help="exit non-zero unless every configured endpoint is serving",
    )
    args = parser.parse_args(argv)

    report = gpu_gate_status()

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"status : {report.status}")
        print(f"gpu metrics measured : {report.gpu_metrics_measured}  (this gate never measures throughput)")
        for endpoint in report.endpoints:
            flag = "OK  " if endpoint.reachable and endpoint.weights_present else "MISS"
            print(
                f"  [{flag}] {endpoint.label:14} {endpoint.url:34} "
                f"reachable={endpoint.reachable} weights={endpoint.weights_present}"
            )
        if report.topology:
            topology = report.topology
            print(
                f"  topology: assumed={topology.assumed_gpu_count} observed={topology.observed_gpu_count} "
                f"cuda={topology.cuda_available}"
            )
            for gpu in topology.observed:
                print(f"    gpu{gpu['index']}: {gpu['name']} ({gpu['total_memory_bytes'] // (1024**2)} MiB)")
        print(
            f"  routing: deployment_mode={report.routing.get('deployment_mode')} "
            f"complex_tier_live={report.routing.get('complex_tier_reachable_at_runtime')}"
        )
        if report.reasons:
            print("\nreasons:")
            for reason in report.reasons:
                print(f"  - {reason}")

    if args.require_live and report.status != STATUS_SATISFIED:
        return report.exit_code
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
