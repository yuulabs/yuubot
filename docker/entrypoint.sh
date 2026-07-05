#!/usr/bin/env bash
set -euo pipefail

YUU_DATA_DIR="${YUU_DATA_DIR:-/data}"
CONFIG_PATH="${YUUBOT_CONFIG:-/config/config.yaml}"
YUUBOT_HOST="${YUUBOT_HOST:-0.0.0.0}"
YUUBOT_PORT="${YUUBOT_PORT:-8765}"

mkdir -p "$YUU_DATA_DIR/db" "$YUU_DATA_DIR/workspace" "$YUU_DATA_DIR/logs" "$YUU_DATA_DIR/kv" "$YUU_DATA_DIR/published"

# Allow compose `command:` overrides (e.g. ytrace ui for the traces-ui service)
if [[ $# -gt 0 ]]; then
    exec "$@"
fi

exec ybot serve "$CONFIG_PATH" --host "$YUUBOT_HOST" --port "$YUUBOT_PORT"
