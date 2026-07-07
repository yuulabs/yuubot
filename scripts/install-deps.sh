#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
DEPS_FILE="$SCRIPT_DIR/deps.yaml"

usage() {
    cat <<EOF
Usage: install-deps.sh [--skip-web-build]

Install project dependencies from scripts/deps.yaml.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-web-build)
            export SKIP_WEB_BUILD=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'error: unknown argument: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

[[ -f "$DEPS_FILE" ]] || {
    printf 'error: missing %s\n' "$DEPS_FILE" >&2
    exit 1
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1
}

need_cmd python3 || {
    printf 'error: python3 is required to parse deps.yaml\n' >&2
    exit 1
}

mapfile -t STEPS < <(DEPS_FILE="$DEPS_FILE" python3 - <<'PY'
import os
import sys

try:
    import yaml
except ImportError:
    print("error: PyYAML is required; run uv sync first", file=sys.stderr)
    sys.exit(1)

deps_file = os.environ["DEPS_FILE"]
with open(deps_file, encoding="utf-8") as handle:
    data = yaml.safe_load(handle) or {}

steps = data.get("steps")
if not isinstance(steps, list) or not steps:
    print("error: deps.yaml must define a non-empty steps list", file=sys.stderr)
    sys.exit(1)

for index, step in enumerate(steps):
    if not isinstance(step, dict):
        print(f"error: step {index} must be a mapping", file=sys.stderr)
        sys.exit(1)
    step_id = step.get("id")
    cwd = step.get("cwd")
    run = step.get("run")
    skip_when = step.get("skip_when")
    if not isinstance(step_id, str) or not step_id:
        print(f"error: step {index} missing id", file=sys.stderr)
        sys.exit(1)
    if not isinstance(cwd, str) or not cwd:
        print(f"error: step {index} missing cwd", file=sys.stderr)
        sys.exit(1)
    if not isinstance(run, str) or not run.strip():
        print(f"error: step {index} missing run", file=sys.stderr)
        sys.exit(1)
    if skip_when is not None and not isinstance(skip_when, str):
        print(f"error: step {index} skip_when must be a string", file=sys.stderr)
        sys.exit(1)
    fields = [step_id, cwd, run.replace("\n", "\\n")]
    if skip_when:
        fields.append(skip_when)
    print("\x1f".join(fields))
PY
)

for entry in "${STEPS[@]}"; do
    IFS=$'\x1f' read -r step_id step_cwd step_run step_skip_when <<<"$entry"
    step_run="${step_run//$'\\n'/$'\n'}"
    if [[ -n "${step_skip_when:-}" && "${!step_skip_when:-}" == "1" ]]; then
        printf '==> skipping %s (%s=1)\n' "$step_id" "$step_skip_when"
        continue
    fi
    printf '\n==> %s\n' "$step_id"
    (
        cd "$REPO_ROOT/$step_cwd"
        bash -c "$step_run"
    )
done

printf '\n==> dependencies installed\n'
