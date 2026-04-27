#!/usr/bin/env bash
set -euo pipefail

export YUU_DEPLOYMENT_MODE="${YUU_DEPLOYMENT_MODE:-container}"
export YUU_WORKSPACE_ROOT="${YUU_WORKSPACE_ROOT:-/workspace}"
export TZ="${TZ:-Asia/Shanghai}"

# Allow compose `command:` overrides (e.g. ytrace ui for the traces-ui service)
if [[ $# -gt 0 ]]; then
    exec "$@"
fi

CONFIG_PATH="${YUUBOT_CONFIG:-/config/config.yaml}"
export YUUBOT_CONFIG="$CONFIG_PATH"

mkdir -p /data/yuubot /data/yuuagents "$YUU_WORKSPACE_ROOT" /app/napcat/config /app/.config/QQ

recorder_pid=""
daemon_pid=""

cleanup() {
    set +e
    for pid in "$daemon_pid" "$recorder_pid"; do
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ybot -c "$CONFIG_PATH" _recorder &
recorder_pid=$!

for _ in $(seq 1 30); do
    if timeout 1 bash -lc '</dev/tcp/127.0.0.1/8767' 2>/dev/null; then
        break
    fi
    sleep 1
done

ybot -c "$CONFIG_PATH" up &
daemon_pid=$!

wait -n "$recorder_pid" "$daemon_pid"
