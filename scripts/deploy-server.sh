#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

CONFIG_DIR="${YUUBOT_CONFIG_DIR:-/etc/yuubot}"
CONFIG_FILE="${YUUBOT_CONFIG:-$CONFIG_DIR/config.yaml}"
ENV_FILE="${YUUBOT_ENV_FILE:-$CONFIG_DIR/yuubot.env}"
DATA_DIR="${YUU_DATA_DIR:-/var/lib/yuubot}"
YUUBOT_PORT="${YUUBOT_PORT:-8765}"
YUUBOT_PUBLIC_PORT="${YUUBOT_PUBLIC_PORT:-8766}"
YUUBOT_TRUSTED_ADMIN_PORT="${YUUBOT_TRUSTED_ADMIN_PORT:-8767}"
ADMIN_DOMAIN="${YUUBOT_ADMIN_DOMAIN:-}"
PUBLIC_URL="${YUUBOT_PUBLIC_URL:-}"
CADDYFILE="${YUUBOT_CADDYFILE:-/etc/caddy/Caddyfile}"
CADDY_CONF_DIR="${YUUBOT_CADDY_CONF_DIR:-/etc/caddy/conf.d}"
CADDY_SITE_FILE="${YUUBOT_CADDY_SITE_FILE:-$CADDY_CONF_DIR/yuubot.caddy}"
SERVICE_USER="${YUUBOT_SERVICE_USER:-$(id -un)}"
SERVICE_GROUP="${YUUBOT_SERVICE_GROUP:-$(id -gn)}"

need_cmd() {
    command -v "$1" >/dev/null 2>&1
}

info() {
    printf '\n==> %s\n' "$*"
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

sudo_write() {
    local path="$1"
    local mode="${2:-0644}"
    local tmp
    tmp="$(mktemp)"
    cat >"$tmp"
    sudo install -D -m "$mode" "$tmp" "$path"
    rm -f "$tmp"
}

rand_token() {
    openssl rand -hex 32
}

require_linux() {
    [[ "$(uname -s)" == "Linux" ]] || die "this deploy script currently supports Linux only"
    [[ "$(id -u)" != "0" ]] || die "run this script as the service user, not root; it will use sudo when needed"
    need_cmd sudo || die "sudo is required"
}

install_system_packages() {
    info "Checking system packages"
    if need_cmd apt-get; then
        sudo apt-get update
        sudo apt-get install -y ca-certificates curl git openssl gpg
    else
        die "automatic package install currently supports Debian/Ubuntu via apt-get"
    fi
}

install_uv() {
    if need_cmd uv; then
        info "uv already installed"
        return
    fi
    info "Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    need_cmd uv || die "uv was installed but is not on PATH; add ~/.local/bin to PATH and rerun"
}

install_node_and_pnpm() {
    if ! need_cmd node; then
        info "Installing Node.js"
        if need_cmd apt-get; then
            curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
            sudo apt-get install -y nodejs
        else
            die "automatic Node.js install currently supports Debian/Ubuntu via apt-get"
        fi
    fi
    info "Enabling pnpm through corepack"
    if need_cmd corepack; then
        if ! corepack enable; then
            info "Retrying corepack enable with sudo"
            sudo corepack enable
        fi
        corepack prepare pnpm@10.12.1 --activate
    elif ! need_cmd pnpm; then
        die "corepack or pnpm is required"
    fi
    need_cmd pnpm || die "pnpm is required after corepack setup"
}

install_caddy() {
    if need_cmd caddy; then
        info "caddy already installed"
        return
    fi
    info "Installing caddy"
    if need_cmd apt-get; then
        sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
        curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/gpg.key" \
            | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt" \
            | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
        sudo apt-get update
        sudo apt-get install -y caddy
    else
        die "automatic caddy install currently supports Debian/Ubuntu via apt-get"
    fi
}

ensure_config() {
    info "Writing yuubot config and environment"
    sudo install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$CONFIG_DIR"
    sudo install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$DATA_DIR"

    if [[ ! -f "$CONFIG_FILE" ]]; then
        sudo_write "$CONFIG_FILE" 0640 <<EOF
data_dir: $DATA_DIR
local_admin_server:
  enabled: true
  host: 127.0.0.1
  port: $YUUBOT_PORT
  url_base: http://127.0.0.1:$YUUBOT_PORT
public_server:
  enabled: false
trusted_admin_server:
  enabled: true
  host: 127.0.0.1
  port: $YUUBOT_TRUSTED_ADMIN_PORT
  url_base: http://127.0.0.1:$YUUBOT_TRUSTED_ADMIN_PORT
  auth:
    mode: proxy
trusted_proxies: [127.0.0.1]
EOF
        sudo chown "root:$SERVICE_GROUP" "$CONFIG_FILE"
    else
        info "Keeping existing $CONFIG_FILE"
        reject_legacy_config
    fi

    if [[ ! -f "$ENV_FILE" ]]; then
        sudo_write "$ENV_FILE" 0640 <<EOF
YUU_DATA_DIR=$DATA_DIR
EOF
        sudo chown "root:$SERVICE_GROUP" "$ENV_FILE"
    else
        info "Keeping existing $ENV_FILE"
    fi
}

reject_legacy_config() {
    if sudo grep -Eq '^[[:space:]]*(admin|database|paths|secrets):[[:space:]]*$' "$CONFIG_FILE"; then
        die "$CONFIG_FILE uses the old split-service config shape. Replace it with the single-process config from docs/server-deploy.md."
    fi
}

normalize_url_base() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value%/}"
    if [[ "$value" != http://* && "$value" != https://* ]]; then
        value="https://$value"
    fi
    printf '%s' "$value"
}

host_from_url_base() {
    local value="$1"
    value="${value#https://}"
    value="${value#http://}"
    value="${value%%/*}"
    value="${value%%:*}"
    printf '%s' "$value"
}

update_listener_config() {
    local admin_url_base="$1"
    local public_enabled="$2"
    local public_url_base="$3"
    local tmp
    tmp="$(mktemp)"
    sudo cat "$CONFIG_FILE" >"$tmp"
    (
        cd "$REPO_ROOT"
        uv run python - "$tmp" "$admin_url_base" "$public_enabled" "$public_url_base" "$YUUBOT_PUBLIC_PORT" "$YUUBOT_TRUSTED_ADMIN_PORT" <<'PY'
import sys
from pathlib import Path

import yaml

config_path = Path(sys.argv[1])
admin_url_base = sys.argv[2]
public_enabled = sys.argv[3] == "true"
public_url_base = sys.argv[4]
public_port = int(sys.argv[5])
trusted_port = int(sys.argv[6])

with config_path.open(encoding="utf-8") as handle:
    data = yaml.safe_load(handle) or {}
if not isinstance(data, dict):
    raise SystemExit("config must be a mapping")

trusted = data.get("trusted_admin_server")
if not isinstance(trusted, dict):
    trusted = {}
trusted["enabled"] = True
trusted.setdefault("host", "127.0.0.1")
trusted["port"] = trusted_port
trusted["url_base"] = admin_url_base
trusted.setdefault("auth", {"mode": "proxy"})
data["trusted_admin_server"] = trusted

data["public_server"] = {
    "enabled": public_enabled,
    "host": "127.0.0.1",
    "port": public_port,
    "url_base": public_url_base if public_enabled else "",
}

with config_path.open("w", encoding="utf-8") as handle:
    yaml.safe_dump(data, handle, sort_keys=False)
PY
    )
    sudo install -m 0640 -o root -g "$SERVICE_GROUP" "$tmp" "$CONFIG_FILE"
    rm -f "$tmp"
}

prompt_public_server() {
    if [[ -n "$PUBLIC_URL" ]]; then
        PUBLIC_URL="$(normalize_url_base "$PUBLIC_URL")"
        return 0
    fi

    local answer public_input
    read -r -p "Enable public server for Share pages and app webhooks? [y/N]: " answer
    case "$answer" in
        y|Y|yes|YES)
            ;;
        *)
            PUBLIC_URL=""
            return 0
            ;;
    esac

    read -r -p "Public URL, e.g. public.example.com or https://public.example.com: " public_input
    [[ -n "$public_input" ]] || die "public URL is required when public server is enabled"
    PUBLIC_URL="$(normalize_url_base "$public_input")"
}

write_caddy_site() {
    local admin_domain="$1"
    local username="$2"
    local hash="$3"
    local public_domain="${4:-}"

    sudo install -d -m 0755 "$CADDY_CONF_DIR"
    if [[ -n "$public_domain" ]]; then
        sudo_write "$CADDY_SITE_FILE" 0644 <<EOF
$admin_domain {
    basic_auth {
        $username $hash
    }

    reverse_proxy 127.0.0.1:$YUUBOT_TRUSTED_ADMIN_PORT {
        header_up X-Forwarded-User {http.auth.user.id}
    }
}

$public_domain {
    @admin_api path /api/*
    respond @admin_api 404

    reverse_proxy 127.0.0.1:$YUUBOT_PUBLIC_PORT
}
EOF
    else
        sudo_write "$CADDY_SITE_FILE" 0644 <<EOF
$admin_domain {
    basic_auth {
        $username $hash
    }

    reverse_proxy 127.0.0.1:$YUUBOT_TRUSTED_ADMIN_PORT {
        header_up X-Forwarded-User {http.auth.user.id}
    }
}
EOF
    fi

    if [[ ! -f "$CADDYFILE" ]]; then
        sudo_write "$CADDYFILE" 0644 <<EOF
import $CADDY_CONF_DIR/*.caddy
EOF
    elif ! sudo grep -Eq "^[[:space:]]*import[[:space:]]+$CADDY_CONF_DIR/\\*\\.caddy" "$CADDYFILE"; then
        printf '\nimport %s/*.caddy\n' "$CADDY_CONF_DIR" | sudo tee -a "$CADDYFILE" >/dev/null
    fi

    sudo caddy validate --config "$CADDYFILE"
    sudo systemctl enable --now caddy
    sudo systemctl reload caddy
}

install_app_dependencies() {
    info "Installing project dependencies"
    "$REPO_ROOT/scripts/install-deps.sh"
}

run_database_migrations() {
    info "Running database migrations"
    (
        set -a
        # shellcheck disable=SC1090
        source "$ENV_FILE"
        set +a
        cd "$REPO_ROOT"
        uv run ybot migrate "$CONFIG_FILE" --json
    )
}

validate_deploy() {
    info "Validating deployment"
    (
        set -a
        # shellcheck disable=SC1090
        source "$ENV_FILE"
        set +a
        cd "$REPO_ROOT"
        uv run ybot check "$CONFIG_FILE" --json
    )
}

install_systemd_units() {
    info "Installing systemd units"
    local uv_path uv_dir service_path
    uv_path="$(command -v uv)"
    uv_dir="$(dirname "$uv_path")"
    service_path="$uv_dir:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    sudo rm -f /etc/systemd/system/yuubot-daemon.service /etc/systemd/system/yuubot-admin.service

    sudo_write /etc/systemd/system/yuubot.service 0644 <<EOF
[Unit]
Description=yuubot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$REPO_ROOT
EnvironmentFile=$ENV_FILE
Environment="PATH=$service_path"
ExecStart=$uv_path run ybot serve $CONFIG_FILE --host 127.0.0.1 --port $YUUBOT_PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl disable --now yuubot-daemon.service yuubot-admin.service >/dev/null 2>&1 || true
    sudo systemctl enable yuubot.service
    sudo systemctl stop yuubot.service >/dev/null 2>&1 || true
    run_database_migrations
    validate_deploy
    sudo systemctl restart yuubot.service
}

prompt_caddy_config() {
    info "Configuring listeners, yuubot config, and Caddy HTTPS"
    local domain username password password_confirm hash admin_url_base public_domain

    if [[ -f "$CADDY_SITE_FILE" ]]; then
        local target_pattern answer
        target_pattern="^[[:space:]]*reverse_proxy[[:space:]]+127\\.0\\.0\\.1:$YUUBOT_TRUSTED_ADMIN_PORT([[:space:]]|\\{|$)"
        if sudo grep -Eq "$target_pattern" "$CADDY_SITE_FILE"; then
            read -r -p "Existing $CADDY_SITE_FILE found. Reconfigure Caddy and listeners? [y/N]: " answer
            case "$answer" in
                y|Y|yes|YES)
                    ;;
                *)
                    info "Keeping existing Caddy yuubot site config"
                    reject_legacy_config
                    if [[ -f "$CADDYFILE" ]]; then
                        sudo caddy validate --config "$CADDYFILE"
                        sudo systemctl enable --now caddy
                        sudo systemctl reload caddy
                    fi
                    return 0
                    ;;
            esac
        else
            info "Existing Caddy site does not proxy to 127.0.0.1:$YUUBOT_TRUSTED_ADMIN_PORT; regenerating it"
        fi
    fi

    if [[ -n "$ADMIN_DOMAIN" ]]; then
        domain="$ADMIN_DOMAIN"
    else
        read -r -p "Admin domain, e.g. admin.example.com: " domain
    fi
    [[ -n "$domain" ]] || die "admin domain is required"
    admin_url_base="$(normalize_url_base "$domain")"
    domain="$(host_from_url_base "$admin_url_base")"

    prompt_public_server
    if [[ -n "$PUBLIC_URL" ]]; then
        public_domain="$(host_from_url_base "$PUBLIC_URL")"
    else
        public_domain=""
    fi

    read -r -p "Admin username [admin]: " username
    username="${username:-admin}"

    while true; do
        read -r -s -p "Admin passphrase/password: " password
        printf '\n'
        [[ -n "$password" ]] || {
            printf 'password cannot be empty\n' >&2
            continue
        }
        read -r -s -p "Confirm passphrase/password: " password_confirm
        printf '\n'
        [[ "$password" == "$password_confirm" ]] && break
        printf 'passwords did not match\n' >&2
    done

    hash="$(caddy hash-password --plaintext "$password")"
    unset password password_confirm

    update_listener_config "$admin_url_base" "$([[ -n "$PUBLIC_URL" ]] && printf true || printf false)" "${PUBLIC_URL:-}"
    write_caddy_site "$domain" "$username" "$hash" "$public_domain"

    cat <<EOF

Cloudflare / DNS remaining work:
  1. Add an A record: $domain -> this server's public IPv4.
EOF
    if [[ -n "$public_domain" ]]; then
        cat <<EOF
  2. Add an A record: $public_domain -> this server's public IPv4.
  3. For first certificate issuance, use DNS only. After HTTPS works, Proxied is OK.
  4. Set Cloudflare SSL/TLS mode to Full (strict).
  5. Ensure the server firewall allows 80/tcp and 443/tcp.
EOF
    else
        cat <<EOF
  2. For first certificate issuance, use DNS only. After HTTPS works, Proxied is OK.
  3. Set Cloudflare SSL/TLS mode to Full (strict).
  4. Ensure the server firewall allows 80/tcp and 443/tcp.
EOF
    fi

    cat <<EOF

Service checks:
  sudo systemctl status yuubot caddy
  sudo journalctl -u yuubot -f

Open:
  $admin_url_base
EOF
    if [[ -n "$PUBLIC_URL" ]]; then
        printf '  %s\n' "$PUBLIC_URL"
    fi
}

main() {
    require_linux
    install_system_packages
    install_uv
    install_node_and_pnpm
    install_caddy
    ensure_config
    install_app_dependencies
    prompt_caddy_config
    install_systemd_units
}

main "$@"
