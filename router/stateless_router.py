"""
无状态请求路由器（readme 4.2 节）

设计原则：
- 遵循 vLLM 原生 continuous batching 机制
- 不在外部实现任何请求队列或优先级抢占逻辑
- 仅做无状态路由分发，所有并发调度完全交由 vLLM 内部 Scheduler 处理
"""

from __future__ import annotations

import logging

import httpx

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)


class StatelessRouter:
    """
    无状态轻量路由器

    路由规则：
    - Rewrite 请求 → vLLM-Rewrite (GPU1, port 8101)
    - Gen 简单请求 → vLLM-Gen-4B (GPU1, port 8102)
    - Gen 复杂请求 → vLLM-Gen-14B (GPU0, port 8100)

    关键约束：
    - 不进行请求排队
    - 不维护优先级队列
    - 不干预 vLLM 内部 continuous batching 决策
    - Rewrite 繁忙时降级为应用层结构兜底，绝不进入阻塞式等待队列
    """

    def __init__(self):
        # 端点 URL 从环境变量（Docker 部署）或 config.json（本地部署）读取
        import os as _os
        rewrite_port = config['gpu1']['models']['vllm_rewrite']['port']
        gen_4b_port = config['gpu1']['models']['vllm_gen_4b']['port']
        gen_14b_port = config['gpu0']['models']['gen_14b']['port']
        self.endpoints = {
            "rewrite": _os.environ.get("VLLM_REWRITE_URL", f"http://localhost:{rewrite_port}"),
            "gen_4b": _os.environ.get("VLLM_GEN_4B_URL", f"http://localhost:{gen_4b_port}"),
            "gen_14b": _os.environ.get("VLLM_GEN_14B_URL", f"http://localhost:{gen_14b_port}"),
        }
        self.timeout_seconds = float(_os.environ.get("VLLM_TIMEOUT_SECONDS", "10.0"))
        self._client = httpx.Client(timeout=self.timeout_seconds)
        logger.info("StatelessRouter 初始化完成")

    def route_chat(
        self,
        endpoint_key: str,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> dict:
        """
        调用 vLLM OpenAI-compatible Chat Completions API

        Args:
            endpoint_key: "rewrite" / "gen_4b" / "gen_14b"
            messages: [{"role": "system/user/assistant", "content": str}]
            max_tokens: 最大输出 token 数
            temperature: 生成温度

        Returns:
            {"content": str, "prefix_cache_hit": bool | None}
            - content: 生成的文本内容
            - prefix_cache_hit: vLLM Prefix Cache 命中状态（从响应头提取）

        Raises:
            连接失败/超时/非200状态码
        """
        base_url = self.endpoints.get(endpoint_key, "")
        if not base_url:
            raise ValueError(f"未知端点: {endpoint_key}")

        url = f"{base_url}/v1/chat/completions"
        payload = {
            "model": "default",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        try:
            resp = self._client.post(url, json=payload)
            resp.raise_for_status()

            # 提取 vLLM Prefix Cache 命中信息（响应头）
            # vLLM 在启用 prefix caching 时会返回 x-prefix-cache-hit 头
            prefix_cache_header = resp.headers.get("x-prefix-cache-hit")
            prefix_cache_hit = None
            if prefix_cache_header is not None:
                prefix_cache_hit = prefix_cache_header.lower() in ("true", "1", "yes")

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return {
                "content": content,
                "prefix_cache_hit": prefix_cache_hit,
            }
        except httpx.TimeoutException:
            logger.error(f"vLLM 端点超时: {endpoint_key} ({self.timeout_seconds}s)")
            raise
        except httpx.ConnectError:
            logger.error(f"vLLM 端点连接失败: {endpoint_key}")
            raise
        except (KeyError, IndexError) as e:
            logger.error(f"vLLM 响应解析异常: {e}")
            raise

    def route_completion(
        self,
        endpoint_key: str,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        """
        调用 vLLM Completions API（兼容旧接口）

        Returns:
            生成的文本内容
        """
        base_url = self.endpoints.get(endpoint_key, "")
        if not base_url:
            raise ValueError(f"未知端点: {endpoint_key}")

        url = f"{base_url}/v1/completions"
        payload = {
            "model": "default",
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        try:
            resp = self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["text"]
        except httpx.TimeoutException:
            logger.error(f"vLLM 端点超时: {endpoint_key}")
            raise
        except httpx.ConnectError:
            logger.error(f"vLLM 端点连接失败: {endpoint_key}")
            raise

    def get_health(self, endpoint_key: str) -> bool:
        """检查端点健康状态"""
        try:
            base_url = self.endpoints.get(endpoint_key, "")
            resp = self._client.get(f"{base_url}/health", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

    def check_any_endpoint_alive(self) -> bool:
        """检查是否有任意端点存活"""
        for key in self.endpoints:
            if self.get_health(key):
                return True
        return False
