#!/usr/bin/env bash
# scripts/fault-injection.sh — 故障演练脚本
# 用法: ./scripts/fault-injection.sh <scenario>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCENARIO="${1:-help}"

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
fail() { echo -e "${RED}[✗]${NC} $1"; }

check_api() {
    echo -n "Checking API health... "
    if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
        echo "OK"
        return 0
    else
        echo "FAILED"
        return 1
    fi
}

case "$SCENARIO" in
    redis-down)
        echo "=== 故障注入: Redis 宕机 ==="
        warn "Stopping Redis..."
        docker compose stop redis
        echo "等待 5 秒..."
        sleep 5
        echo "验证 API 仍可访问 (L1 内存缓存接管)..."
        check_api && log "Redis down 演练通过 — API 仍然可用" || fail "API 不可用"
        echo ""
        echo "恢复 Redis..."
        docker compose start redis
        sleep 3
        check_api && log "Redis 恢复正常" || warn "Redis 恢复中..."
        ;;

    qdrant-down)
        echo "=== 故障注入: Qdrant 不可用 ==="
        warn "Stopping Qdrant..."
        docker compose stop qdrant 2>/dev/null || true
        echo "等待 5 秒..."
        sleep 5
        echo "验证 API 仍可访问 (ES Fallback)..."
        check_api && log "Qdrant down 演练通过 — ES Fallback 生效" || fail "API 不可用"
        echo ""
        echo "恢复 Qdrant..."
        docker compose start qdrant 2>/dev/null || true
        sleep 5
        check_api && log "Qdrant 恢复正常" || warn "Qdrant 恢复中..."
        ;;

    es-down)
        echo "=== 故障注入: Elasticsearch 不可用 ==="
        warn "Stopping Elasticsearch..."
        docker compose stop elasticsearch
        echo "等待 5 秒..."
        sleep 5
        echo "验证 API 仍可访问 (纯向量检索)..."
        check_api && log "ES down 演练通过 — 纯向量检索生效" || fail "API 不可用"
        echo ""
        echo "恢复 Elasticsearch..."
        docker compose start elasticsearch
        sleep 5
        check_api && log "Elasticsearch 恢复正常" || warn "ES 恢复中..."
        ;;

    query-test)
        echo "=== 功能验证: 发送测试查询 ==="
        echo "发送简单查询..."
        RESP=$(curl -sf -X POST http://localhost:8000/api/query \
            -H "Content-Type: application/json" \
            -H "X-User-ID: test_admin" \
            -H "X-Role-Mask: 1" \
            -H "X-Dept-Mask: 0" \
            -d '{"query": "烟酰胺的安全浓度是多少？"}' 2>&1)
        if echo "$RESP" | grep -q "answer\|response"; then
            log "查询测试通过"
            echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP"
        else
            fail "查询测试失败"
            echo "$RESP"
        fi
        ;;

    all)
        echo "=== 运行所有故障演练 ==="
        echo ""
        "$0" redis-down
        echo ""
        "$0" qdrant-down
        echo ""
        "$0" es-down
        echo ""
        "$0" query-test
        echo ""
        log "所有故障演练完成"
        ;;

    help|*)
        echo "化妆品行业 RAG 问答系统 — 故障演练脚本"
        echo ""
        echo "用法: $0 <scenario>"
        echo ""
        echo "可用场景:"
        echo "  redis-down     Redis 宕机演练"
        echo "  qdrant-down    Qdrant 不可用演练"
        echo "  es-down        Elasticsearch 不可用演练"
        echo "  query-test     功能验证测试"
        echo "  all            运行所有演练"
        echo "  help           显示此帮助"
        ;;
esac
