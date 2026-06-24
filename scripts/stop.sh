#!/usr/bin/env bash
# scripts/stop.sh — 停止化妆品行业 RAG 问答系统
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "停止所有服务..."
cd "$PROJECT_DIR"
docker compose -f docker-compose.yml -f docker-compose.gpu.yml down 2>/dev/null || true
docker compose -f docker-compose.yml -f docker-compose.cpu.yml down 2>/dev/null || true
docker compose -f docker-compose.yml down 2>/dev/null || true

echo "✅ 所有服务已停止"
