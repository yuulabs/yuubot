#!/usr/bin/env bash
set -euo pipefail

YUU_DATA_DIR="${YUU_DATA_DIR:-/data}"
CONFIG_PATH="${YUUBOT_CONFIG:-/config/config.yaml}"

mkdir -p "$YUU_DATA_DIR/yuubot"

# Allow compose `command:` overrides (e.g. ytrace ui for the traces-ui service)
if [[ $# -gt 0 ]]; then
    exec "$@"
fi

daemon_pid=""
admin_pid=""

cleanup() {
    set +e
    for pid in "$daemon_pid" "$admin_pid"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Start daemon (background)
ybot --config "$CONFIG_PATH" daemon &
daemon_pid=$!

# Wait for daemon to become ready (poll /healthz)
for _ in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8780/healthz >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Start admin (background, keeps container running)
ybot --config "$CONFIG_PATH" admin &
admin_pid=$!

wait -n "$daemon_pid" "$admin_pid"