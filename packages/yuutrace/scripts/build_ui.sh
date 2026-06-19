#!/usr/bin/env bash
# Build the @yuutrace/ui React app and copy output to cli/_static/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
UI_DIR="$ROOT_DIR/ui"
STATIC_DIR="$ROOT_DIR/src/yuutrace/cli/_static"

echo "==> Building @yuutrace/ui app..."
cd "$UI_DIR"
npm ci
npm run build:app

echo "==> Copying build output to $STATIC_DIR..."
# Clean old assets but keep __init__.py
find "$STATIC_DIR" -mindepth 1 ! -name '__init__.py' -delete 2>/dev/null || true
cp -r dist/app/* "$STATIC_DIR/"

echo "==> Done."
