#!/usr/bin/env bash
# ============================================================================
# 部署验证脚本 — 化妆品行业 RAG 问答系统
# ============================================================================
# 用法: ./scripts/verify-deployment.sh [BASE_URL]
# 默认 BASE_URL: http://localhost:8000
# ============================================================================

set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
PASS=0
FAIL=0
WARN=0

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; ((PASS++)); }
fail() { echo -e "  ${RED}✗${NC} $1"; ((FAIL++)); }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; ((WARN++)); }

echo "============================================"
echo " 部署验证: ${BASE_URL}"
echo "============================================"

# ── 1. Docker 容器健康检查 ─────────────────────────────
echo ""
echo "▸ Docker 容器状态"
if command -v docker &>/dev/null; then
    UNHEALTHY=$(docker ps --filter "health=unhealthy" --format "{{.Names}}" 2>/dev/null || true)
    if [ -z "$UNHEALTHY" ]; then
        pass "所有容器健康"
    else
        fail "不健康容器: $UNHEALTHY"
    fi
    RUNNING=$(docker ps --format "{{.Names}}" | wc -l)
    echo "  运行中容器数: ${RUNNING}"
else
    warn "docker 不可用，跳过容器检查"
fi

# ── 2. API Gateway 健康检查 ────────────────────────────
echo ""
echo "▸ API Gateway 健康检查"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/api/health" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    pass "API Gateway 响应 200"
else
    fail "API Gateway 响应 ${HTTP_CODE}"
fi

# ── 3. Prometheus 指标端点 ─────────────────────────────
echo ""
echo "▸ Prometheus 指标"
METRICS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/api/metrics" 2>/dev/null || echo "000")
if [ "$METRICS_CODE" = "200" ]; then
    pass "/api/metrics 端点可用"
    # 检查关键指标是否存在
    METRICS_BODY=$(curl -s "${BASE_URL}/api/metrics" 2>/dev/null || echo "")
    for metric in "rag_uptime_seconds" "rag_cache" "rag_rewrite"; do
        if echo "$METRICS_BODY" | grep -q "$metric"; then
            pass "指标 ${metric} 存在"
        else
            warn "指标 ${metric} 未找到"
        fi
    done
else
    fail "/api/metrics 端点响应 ${METRICS_CODE}"
fi

# ── 4b. Auth Metadata 端点 ──────────────────────────────
echo ""
echo "▸ Auth Metadata 端点"
METADATA_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/api/auth/metadata" 2>/dev/null || echo "000")
if [ "$METADATA_CODE" = "200" ]; then
    pass "/api/auth/metadata 响应 200"
else
    fail "/api/auth/metadata 响应 ${METADATA_CODE}"
fi

# ── 5. Stats 端点 ──────────────────────────────────────
echo ""
echo "▸ Stats 端点"
STATS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/api/stats" 2>/dev/null || echo "000")
if [ "$STATS_CODE" = "200" ]; then
    pass "/api/stats 响应 200"
else
    fail "/api/stats 响应 ${STATS_CODE}"
fi

# ── 6. 安全头检查 ──────────────────────────────────────
echo ""
echo "▸ 安全头检查"
HEADERS=$(curl -sI "${BASE_URL}/" 2>/dev/null || echo "")
for header in "X-Content-Type-Options" "X-Frame-Options" "X-XSS-Protection"; do
    if echo "$HEADERS" | grep -qi "$header"; then
        pass "${header} 已设置"
    else
        warn "${header} 未设置"
    fi
done

# ── 7. 基础查询功能 ────────────────────────────────────
echo ""
echo "▸ 基础查询功能"
QUERY_RESULT=$(curl -s -X POST "${BASE_URL}/api/query" \
    -H "Content-Type: application/json" \
    -d '{"query": "烟酰胺的推荐用量", "user_id": "verify-script"}' \
    2>/dev/null || echo "")
if echo "$QUERY_RESULT" | grep -q "answer"; then
    pass "查询接口返回 answer"
else
    warn "查询接口未返回预期结果（可能需要认证）"
fi

# ── 8. Grafana ─────────────────────────────────────────
echo ""
echo "▸ Grafana 监控"
GRAFANA_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}:3000/api/health" 2>/dev/null || echo "000")
if [ "$GRAFANA_CODE" = "200" ]; then
    pass "Grafana 健康"
else
    warn "Grafana 不可达 (${GRAFANA_CODE})"
fi

# ── 汇总 ───────────────────────────────────────────────
echo ""
echo "============================================"
echo " 结果: ${GREEN}${PASS} 通过${NC}  ${RED}${FAIL} 失败${NC}  ${YELLOW}${WARN} 警告${NC}"
echo "============================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
