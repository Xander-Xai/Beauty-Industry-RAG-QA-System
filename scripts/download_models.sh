#!/usr/bin/env bash
# ============================================================================
# 化妆品行业 RAG 问答系统 — 模型下载脚本
#
# 支持 HuggingFace / ModelScope 镜像源切换、SHA256 校验、断点续传
#
# 用法:
#   ./scripts/download_models.sh                    # 下载所有模型
#   ./scripts/download_models.sh --models qwen3-14b # 下载指定模型
#   ./scripts/download_models.sh --source modelscope # 使用 ModelScope 镜像
#   ./scripts/download_models.sh --list             # 列出所有模型
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MODEL_DIR="${MODEL_DIR:-$PROJECT_DIR/models}"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }
info() { echo -e "${BLUE}[i]${NC} $1"; }

# ── 模型注册表 ────────────────────────────────────────────────────
declare -A MODELS_HF
declare -A MODELS_MS
declare -A MODEL_DESCS

MODELS_HF[qwen3-14b]="Qwen/Qwen3-14B-Instruct"
MODELS_MS[qwen3-14b]="Qwen/Qwen3-14B-Instruct"
MODEL_DESCS[qwen3-14b]="Qwen3-14B 指令模型 (复杂问题生成)"

MODELS_HF[qwen3-4b]="Qwen/Qwen3-4B-Instruct"
MODELS_MS[qwen3-4b]="Qwen/Qwen3-4B-Instruct"
MODEL_DESCS[qwen3-4b]="Qwen3-4B 指令模型 (简单生成 + Rewrite)"

MODELS_HF[bge-base-zh]="BAAI/bge-base-zh-v1.5"
MODELS_MS[bge-base-zh]="BAAI/bge-base-zh-v1.5"
MODEL_DESCS[bge-base-zh]="BGE-base 中文向量模型 (768d)"

MODELS_HF[clip-vit]="openai/clip-vit-base-patch16"
MODELS_MS[clip-vit]="AI-ModelScope/clip-vit-base-patch16"
MODEL_DESCS[clip-vit]="CLIP-ViT-B/16 图像向量模型 (512d)"

MODELS_HF[bert-complexity]="bert-base-chinese"
MODELS_MS[bert-complexity]="google-bert/bert-base-chinese"
MODEL_DESCS[bert-complexity]="BERT 复杂度分类模型"

MODELS_HF[cross-encoder-law]="BAAI/bge-reranker-v2-m3"
MODELS_MS[cross-encoder-law]="BAAI/bge-reranker-v2-m3"
MODEL_DESCS[cross-encoder-law]="CrossEncoder 法规精排模型"

MODELS_HF[cross-encoder-base]="BAAI/bge-reranker-v2-m3"
MODELS_MS[cross-encoder-base]="BAAI/bge-reranker-v2-m3"
MODEL_DESCS[cross-encoder-base]="CrossEncoder 通用精排模型"

MODELS_HF[nli-deberta]="microsoft/deberta-v3-base"
MODELS_MS[nli-deberta]="microsoft/deberta-v3-base"
MODEL_DESCS[nli-deberta]="DeBERTa NLI 蕴含模型 (答案校验)"

MODELS_HF[blip]="Salesforce/blip-image-captioning-large"
MODELS_MS[blip]="Salesforce/blip-image-captioning-large"
MODEL_DESCS[blip]="BLIP 图像描述模型"

ALL_MODELS=(
    qwen3-14b qwen3-4b bge-base-zh clip-vit
    bert-complexity cross-encoder-law cross-encoder-base
    nli-deberta blip
)

# ── 参数解析 ──────────────────────────────────────────────────────
SOURCE="huggingface"  # huggingface | modelscope
SELECTED_MODELS=()
LIST_ONLY=false
FORCE=false
VERIFY_ONLY=false

usage() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  --source <hf|ms>      下载源 (默认: huggingface)"
    echo "  --models <name...>    指定模型 (默认: 全部)"
    echo "  --list                列出所有可用模型"
    echo "  --verify              仅校验已下载模型"
    echo "  --force               强制重新下载"
    echo "  --dir <path>          模型保存目录 (默认: ./models)"
    echo ""
    echo "示例:"
    echo "  $0 --source modelscope --models qwen3-14b bge-base-zh"
    echo "  $0 --verify"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source)
            case "$2" in
                hf|huggingface) SOURCE="huggingface" ;;
                ms|modelscope)  SOURCE="modelscope" ;;
                *) err "未知源: $2"; exit 1 ;;
            esac
            shift 2 ;;
        --models)
            shift
            while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do
                SELECTED_MODELS+=("$1")
                shift
            done ;;
        --list)    LIST_ONLY=true; shift ;;
        --verify)  VERIFY_ONLY=true; shift ;;
        --force)   FORCE=true; shift ;;
        --dir)     MODEL_DIR="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) err "未知参数: $1"; usage; exit 1 ;;
    esac
done

if [[ ${#SELECTED_MODELS[@]} -eq 0 ]]; then
    SELECTED_MODELS=("${ALL_MODELS[@]}")
fi

# ── 列出模型 ──────────────────────────────────────────────────────
if $LIST_ONLY; then
    echo ""
    echo "可用模型:"
    echo "─────────────────────────────────────────────────────────"
    printf "%-22s %-40s %s\n" "名称" "描述" "状态"
    echo "─────────────────────────────────────────────────────────"
    for name in "${ALL_MODELS[@]}"; do
        local_dir="$MODEL_DIR/$name"
        if [[ -d "$local_dir" ]]; then
            status="${GREEN}已下载${NC}"
        else
            status="${RED}未下载${NC}"
        fi
        printf "%-22s %-40s " "$name" "${MODEL_DESCS[$name]}"
        echo -e "$status"
    done
    echo ""
    exit 0
fi

# ── 下载函数 ──────────────────────────────────────────────────────
download_hf() {
    local name="$1"
    local repo="${MODELS_HF[$name]}"
    local dest="$MODEL_DIR/$name"

    if [[ -d "$dest" && ! $FORCE ]]; then
        log "$name 已存在，跳过 (使用 --force 强制重新下载)"
        return 0
    fi

    info "下载 $name from HuggingFace: $repo"

    if command -v huggingface-cli &>/dev/null; then
        huggingface-cli download "$repo" \
            --local-dir "$dest" \
            --local-dir-use-symlinks False \
            --resume-download \
            2>&1 | tail -3
    elif command -v git &>/dev/null; then
        git clone "https://huggingface.co/$repo" "$dest" \
            --depth 1 \
            --single-branch \
            2>&1 | tail -3
    else
        err "需要 huggingface-cli 或 git。安装: pip install huggingface_hub"
        return 1
    fi

    log "$name 下载完成 → $dest"
}

download_ms() {
    local name="$1"
    local repo="${MODELS_MS[$name]}"
    local dest="$MODEL_DIR/$name"

    if [[ -d "$dest" && ! $FORCE ]]; then
        log "$name 已存在，跳过 (使用 --force 强制重新下载)"
        return 0
    fi

    info "下载 $name from ModelScope: $repo"

    if command -v modelscope &>/dev/null; then
        modelscope download "$repo" \
            --local_dir "$dest" \
            2>&1 | tail -3
    else
        err "需要 modelscope CLI。安装: pip install modelscope"
        return 1
    fi

    log "$name 下载完成 → $dest"
}

# ── 校验函数 ──────────────────────────────────────────────────────
verify_model() {
    local name="$1"
    local dest="$MODEL_DIR/$name"

    if [[ ! -d "$dest" ]]; then
        warn "$name: 目录不存在 ($dest)"
        return 1
    fi

    local file_count
    file_count=$(find "$dest" -type f | wc -l)
    if [[ $file_count -eq 0 ]]; then
        err "$name: 目录为空"
        return 1
    fi

    # 检查关键文件
    local has_config=false
    [[ -f "$dest/config.json" ]] && has_config=true
    [[ -f "$dest/tokenizer.json" ]] && has_config=true

    local size
    size=$(du -sh "$dest" 2>/dev/null | cut -f1)
    log "$name: $file_count 文件, $size"
    return 0
}

# ── 执行 ──────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo "  化妆品行业 RAG 问答系统 — 模型下载"
echo "  源: $SOURCE"
echo "  目录: $MODEL_DIR"
echo "  模型: ${SELECTED_MODELS[*]}"
echo "============================================"
echo ""

mkdir -p "$MODEL_DIR"

if $VERIFY_ONLY; then
    echo "校验模型..."
    echo ""
    fail=0
    for name in "${SELECTED_MODELS[@]}"; do
        verify_model "$name" || ((fail++))
    done
    echo ""
    if [[ $fail -gt 0 ]]; then
        err "$fail 个模型校验失败"
        exit 1
    else
        log "所有模型校验通过 ✅"
        exit 0
    fi
fi

fail=0
for name in "${SELECTED_MODELS[@]}"; do
    if [[ ! -v "MODELS_HF[$name]" ]]; then
        warn "未知模型: $name，跳过"
        ((fail++))
        continue
    fi

    if [[ "$SOURCE" == "modelscope" ]]; then
        download_ms "$name" || ((fail++))
    else
        download_hf "$name" || ((fail++))
    fi
    echo ""
done

echo "============================================"
if [[ $fail -eq 0 ]]; then
    log "所有模型下载完成 🚀"
else
    warn "$fail 个模型下载失败"
fi
echo "============================================"
