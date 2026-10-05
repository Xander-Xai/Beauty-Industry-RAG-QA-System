"""Regression guard for runtime-exposed API/product metadata.

The OpenAPI document is what an evaluator or integrator reads before ever
opening the source. It must therefore stay inside the repository's evidence
boundary: the 4B/14B vLLM GPU topology is `PENDING` (weights absent, `vllm`
not installed) and the RTX A5000 x2 serving host is `HISTORICAL_PRODUCTION`,
so neither may be advertised as a property of this running application.
"""

from __future__ import annotations

import re

import pytest

# Claims that would equate historical production hardware with the runtime
# this repository can actually verify.
_FORBIDDEN_TOPOLOGY_CLAIMS = (
    re.compile(r"双\s*GPU", re.IGNORECASE),
    re.compile(r"dual[\s-]*GPU", re.IGNORECASE),
    re.compile(r"双\s*卡"),
    re.compile(r"2\s*(?:张|个)\s*GPU", re.IGNORECASE),
    re.compile(r"A5000"),
    re.compile(r"GPU\s*[×xX*]\s*2", re.IGNORECASE),
)


@pytest.fixture(scope="module")
def openapi_info() -> dict:
    from app import app

    return app.openapi()["info"]


def test_openapi_info_keeps_configured_name_and_version(openapi_info: dict) -> None:
    """Title and version stay sourced from config; only the claim changed."""
    from common.config import get_config_dict

    config = get_config_dict()

    assert openapi_info["title"] == config["system"]["name"]
    assert openapi_info["version"] == config["system"]["version"]


def test_openapi_description_states_multimodal_rag_api(openapi_info: dict) -> None:
    """The replacement wording is the repo-verifiable one."""
    description = openapi_info["description"]

    assert "多模态检索增强生成" in description
    assert "RAG" in description
    assert "知识问答 API" in description
    assert openapi_info["title"] in description


def test_openapi_description_makes_no_unverified_gpu_topology_claim(openapi_info: dict) -> None:
    """No dual-GPU production topology may reappear in runtime metadata."""
    description = openapi_info["description"]

    offenders = [pattern.pattern for pattern in _FORBIDDEN_TOPOLOGY_CLAIMS if pattern.search(description)]
    assert offenders == [], f"OpenAPI description re-asserts an unverified GPU topology: {offenders}"


def test_module_level_description_matches_generated_schema() -> None:
    """`app.description` and the generated OpenAPI info cannot drift apart."""
    from app import app

    assert app.description == app.openapi()["info"]["description"]
