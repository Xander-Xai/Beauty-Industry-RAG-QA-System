#!/bin/bash
# vLLM 服务初始化脚本

set -e

echo "=== vLLM 服务初始化 ==="

wait_for_vllm() {
    local url=$1
    local name=$2
    local max_attempts=30
    local attempt=1

    echo "等待 $name 启动..."
    while [ $attempt -le $max_attempts ]; do
        if curl -sf "$url/health" > /dev/null 2>&1; then
            echo "$name 已就绪"
            return 0
        fi
        echo "  尝试 $attempt/$max_attempts..."
        sleep 5
        attempt=$((attempt + 1))
    done
    echo "$name 启动超时"
    return 1
}

wait_for_vllm "http://localhost:8100" "vLLM-Gen-14B"
wait_for_vllm "http://localhost:8101" "vLLM-Rewrite"
wait_for_vllm "http://localhost:8102" "vLLM-Gen-4B"

echo "=== 所有 vLLM 服务已就绪 ==="
