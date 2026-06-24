#!/usr/bin/env bash
# ============================================================================
# Beauty Industry RAG QA System — Build Script
# ============================================================================
# Builds the frontend and copies output to the backend static directory,
# then optionally builds the Docker image.
#
# Usage:
#   ./build.sh              # Build frontend + copy to static/
#   ./build.sh --docker     # Also build Docker image
#   ./build.sh --clean      # Clean build
# ============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
STATIC_DIR="$PROJECT_ROOT/static"

echo "=== Building Beauty Industry RAG QA System ==="

# ── Step 1: Build frontend ──────────────────────────────────────────────
echo ""
echo "--- Step 1/3: Building frontend (npm install + npm run build) ---"
cd "$PROJECT_ROOT/frontend"
npm install --silent
npm run build
cd "$PROJECT_ROOT"

# ── Step 2: Copy to static/ ─────────────────────────────────────────────
echo ""
echo "--- Step 2/3: Copying frontend/dist -> static/ ---"
rm -rf "$STATIC_DIR"
cp -r "$PROJECT_ROOT/frontend/dist" "$STATIC_DIR"
echo "  Copied $(find "$STATIC_DIR" -type f | wc -l) files to $STATIC_DIR"

# ── Step 3: Docker build (optional) ────────────────────────────────────
if [ "${1:-}" = "--docker" ]; then
    echo ""
    echo "--- Step 3/3: Building Docker image ---"
    docker build -t beauty-industry-rag:latest "$PROJECT_ROOT"
    echo "  Docker image built: beauty-industry-rag:latest"
else
    echo ""
    echo "--- Step 3/3: Skipped (pass --docker to build Docker image) ---"
fi

echo ""
echo "=== Build complete ==="
