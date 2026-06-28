#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

CONFIG_DIR="${YUUBOT_CONFIG_DIR:-/etc/yuubot}"
CONFIG_FILE="${YUUBOT_CONFIG:-$CONFIG_DIR/config.yaml}"
ENV_FILE="${YUUBOT_ENV_FILE:-$CONFIG_DIR/yuubot.env}"
DATA_DIR="${YUU_DATA_DIR:-/var/lib/yuubot}"
ADMIN_PORT="${YUUBOT_ADMIN_PORT:-8781}"
DAEMON_PORT="${YUUBOT_DAEMON_PORT:-8780}"
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
admin:
  host: 127.0.0.1
  port: $ADMIN_PORT
  secret: \${YUU_ADMIN_SECRET}

server:
  daemon_host: 127.0.0.1
  daemon_port: $DAEMON_PORT
  daemon_secret: \${YUU_DAEMON_SECRET}

database:
  path: \${YUU_DATA_DIR}/yuubot/yuubot.db

secrets:
  master_key: \${YUU_SECRET_KEY}

trace:
  enabled: true
  collector_host: 127.0.0.1
  collector_port: 4318

paths:
  data_dir: \${YUU_DATA_DIR}
EOF
        sudo chown "root:$SERVICE_GROUP" "$CONFIG_FILE"
    else
        info "Keeping existing $CONFIG_FILE"
    fi

    if [[ ! -f "$ENV_FILE" ]]; then
        local secret_key admin_secret daemon_secret
        secret_key="$(openssl rand -base64 32)"
        admin_secret="$(rand_token)"
        daemon_secret="$(rand_token)"
        sudo_write "$ENV_FILE" 0640 <<EOF
YUU_DATA_DIR=$DATA_DIR
YUU_SECRET_KEY=$secret_key
YUU_ADMIN_SECRET=$admin_secret
YUU_DAEMON_SECRET=$daemon_secret
EOF
        sudo chown "root:$SERVICE_GROUP" "$ENV_FILE"
    else
        info "Keeping existing $ENV_FILE"
    fi
}

install_app_dependencies() {
    info "Installing Python workspace dependencies"
    (cd "$REPO_ROOT" && uv sync)

    info "Building Admin UI"
    (
        cd "$REPO_ROOT/apps/yuubot/web"
        if [[ -f pnpm-lock.yaml ]]; then
            pnpm install --frozen-lockfile
        else
            pnpm install --no-frozen-lockfile
        fi
        pnpm run build
    )

    info "Validating bootstrap config"
    (
        set -a
        # shellcheck disable=SC1090
        source "$ENV_FILE"
        set +a
        cd "$REPO_ROOT"
        uv run ybot --config "$CONFIG_FILE" check
    )
}

install_systemd_units() {
    info "Installing systemd units"
    local uv_path uv_dir service_path
    uv_path="$(command -v uv)"
    uv_dir="$(dirname "$uv_path")"
    service_path="$uv_dir:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    sudo_write /etc/systemd/system/yuubot-daemon.service 0644 <<EOF
[Unit]
Description=yuubot daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$REPO_ROOT
EnvironmentFile=$ENV_FILE
Environment="PATH=$service_path"
ExecStart=$uv_path run ybot --config $CONFIG_FILE daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    sudo_write /etc/systemd/system/yuubot-admin.service 0644 <<EOF
[Unit]
Description=yuubot admin
After=network-online.target yuubot-daemon.service
Wants=network-online.target
Requires=yuubot-daemon.service

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$REPO_ROOT
EnvironmentFile=$ENV_FILE
Environment="PATH=$service_path"
ExecStart=$uv_path run ybot --config $CONFIG_FILE admin
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable yuubot-daemon.service yuubot-admin.service
    sudo systemctl restart yuubot-daemon.service
    sudo systemctl restart yuubot-admin.service
}

prompt_caddy_config() {
    info "Configuring Caddy HTTPS and Basic Auth"
    local domain username password password_confirm hash

    if [[ -f "$CADDY_SITE_FILE" ]]; then
        local answer
        read -r -p "Existing $CADDY_SITE_FILE found. Reconfigure Caddy? [y/N]: " answer
        case "$answer" in
            y|Y|yes|YES)
                ;;
            *)
                info "Keeping existing Caddy yuubot site config"
                sudo caddy validate --config "$CADDYFILE"
                sudo systemctl enable --now caddy
                sudo systemctl reload caddy
                return
                ;;
        esac
    fi

    read -r -p "Admin domain, e.g. admin.example.com: " domain
    [[ -n "$domain" ]] || die "domain is required"

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

    sudo install -d -m 0755 "$CADDY_CONF_DIR"
    sudo_write "$CADDY_SITE_FILE" 0644 <<EOF
$domain {
    basic_auth {
        $username $hash
    }

    reverse_proxy 127.0.0.1:$ADMIN_PORT
}
EOF

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

    cat <<EOF

Cloudflare / DNS remaining work:
  1. Add an A record: $domain -> this server's public IPv4.
  2. For first certificate issuance, use DNS only. After HTTPS works, Proxied is OK.
  3. Set Cloudflare SSL/TLS mode to Full (strict).
  4. Ensure the server firewall allows 80/tcp and 443/tcp.

Service checks:
  sudo systemctl status yuubot-daemon yuubot-admin caddy
  sudo journalctl -u yuubot-daemon -u yuubot-admin -f

Open:
  https://$domain
EOF
}

main() {
    require_linux
    install_system_packages
    install_uv
    install_node_and_pnpm
    install_caddy
    ensure_config
    install_app_dependencies
    install_systemd_units
    prompt_caddy_config
}

main "$@"
