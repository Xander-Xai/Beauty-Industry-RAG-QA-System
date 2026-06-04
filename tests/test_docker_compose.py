import yaml
import os
import pytest


def test_base_compose_has_infrastructure():
    """主编排文件应包含基础设施服务。"""
    with open("docker-compose.yml") as f:
        config = yaml.safe_load(f)
    services = config.get("services", {})
    assert "redis" in services
    assert "elasticsearch" in services
    assert "app" in services


def test_base_compose_no_vllm():
    """主编排文件不应包含 vLLM 服务（移到 gpu 覆盖层）。"""
    with open("docker-compose.yml") as f:
        config = yaml.safe_load(f)
    services = config.get("services", {})
    vllm_services = [s for s in services if "vllm" in s]
    assert len(vllm_services) == 0


def test_gpu_compose_has_vllm_services():
    """GPU 覆盖层应包含 vLLM 服务。"""
    if not os.path.exists("docker-compose.gpu.yml"):
        pytest.skip("docker-compose.gpu.yml not created yet")
    with open("docker-compose.gpu.yml") as f:
        config = yaml.safe_load(f)
    services = config.get("services", {})
    assert "vllm-gen-14b" in services
    assert "vllm-rewrite" in services
    assert "vllm-gen-4b" in services


def test_cpu_compose_has_no_gpu():
    """CPU 覆盖层不应有任何 GPU 资源声明。"""
    if not os.path.exists("docker-compose.cpu.yml"):
        pytest.skip("docker-compose.cpu.yml not created yet")
    with open("docker-compose.cpu.yml") as f:
        content = f.read()
    assert "CUDA_VISIBLE_DEVICES" not in content
