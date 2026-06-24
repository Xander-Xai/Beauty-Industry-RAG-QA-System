#!/usr/bin/env bash
# scripts/start.sh — 一键启动化妆品行业 RAG 问答系统
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-development}"
COMPOSE_FILES="-f docker-compose.yml"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

echo "============================================"
echo "  化妆品行业 RAG 问答系统 — 一键启动"
echo "  模式: $DEPLOYMENT_MODE"
echo "============================================"
echo ""

# Step 1: 检查环境
echo "Step 1/6  检查环境..."
command -v docker >/dev/null 2>&1 || err "Docker 未安装"
docker compose version >/dev/null 2>&1 || err "Docker Compose 未安装"
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_COUNT=$(nvidia-smi -L 2>/dev/null | wc -l)
    log "检测到 $GPU_COUNT 个 GPU"
else
    GPU_COUNT=0
    warn "未检测到 GPU，将使用 CPU 模式"
    DEPLOYMENT_MODE="development"
fi

# Step 2: 根据模式选择 compose 文件
echo "Step 2/6  选择部署配置..."
if [ "$DEPLOYMENT_MODE" = "production" ] && [ "$GPU_COUNT" -ge 2 ]; then
    COMPOSE_FILES="-f docker-compose.yml -f docker-compose.gpu.yml"
    log "双卡生产模式"
elif [ "$DEPLOYMENT_MODE" = "testing" ] && [ "$GPU_COUNT" -ge 1 ]; then
    COMPOSE_FILES="-f docker-compose.yml -f docker-compose.gpu.yml"
    log "单卡测试模式 (14B 降级到 4B)"
else
    COMPOSE_FILES="-f docker-compose.yml -f docker-compose.cpu.yml"
    DEPLOYMENT_MODE="development"
    log "CPU 开发模式"
fi

# Step 3: 检查模型权重
echo "Step 3/6  检查模型权重..."
MODEL_DIR="${MODEL_DIR:-./models}"
if [ "$DEPLOYMENT_MODE" != "development" ]; then
    REQUIRED_MODELS=("qwen3-14b" "qwen3-4b" "bge-base-zh-v1.5")
    for model in "${REQUIRED_MODELS[@]}"; do
        if [ -d "$PROJECT_DIR/$MODEL_DIR/$model" ]; then
            log "$model ✓"
        else
            warn "$model 未找到 — 部分功能可能不可用"
        fi
    done
else
    log "开发模式，跳过模型检查"
fi

# Step 4: 初始化环境变量
echo "Step 4/6  初始化环境变量..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    log "已从 .env.example 创建 .env"
else
    log ".env 已存在"
fi

# Step 5: 启动基础设施
echo "Step 5/6  启动基础设施..."
cd "$PROJECT_DIR"
docker compose $COMPOSE_FILES up -d redis elasticsearch 2>/dev/null || warn "部分基础设施启动可能需要更多时间"
sleep 3

# Step 6: 启动应用
echo "Step 6/6  启动应用服务..."
if [ "$DEPLOYMENT_MODE" = "production" ]; then
    docker compose $COMPOSE_FILES up -d app vllm-gen-14b vllm-rewrite vllm-gen-4b
elif [ "$DEPLOYMENT_MODE" = "testing" ]; then
    docker compose $COMPOSE_FILES up -d app vllm-rewrite vllm-gen-4b
else
    docker compose $COMPOSE_FILES up -d app
fi

echo ""
echo "============================================"
log "系统就绪 🚀"
echo "  API 文档:  http://localhost:8000/docs"
echo "  健康检查:  http://localhost:8000/api/health"
echo "  部署模式:  $DEPLOYMENT_MODE"
echo "============================================"
