# Server Deployment

yuubot runs as a **single process** (`ybot serve`) that can bind multiple HTTP
listeners defined in config. The deploy script sets up a loopback admin listener
for local operations and a trusted admin listener behind Caddy for remote HTTPS
access.

```text
Internet / Cloudflare DNS
  -> HTTPS
  -> Caddy (TLS reverse proxy on admin domain)
  -> trusted_admin_server on 127.0.0.1:8767
       auth.mode: builtin (Yuubot login page + session cookie)

Loopback (SSH, cron, ybot CLI, Settings apply-update):
  -> local_admin_server on 127.0.0.1:8765
       auth: loopback_bypass (no login on 127.0.0.1)

Optional (disabled by default):
  -> public_server on a separate port for Share pages and app webhooks
```

## Prerequisites

- Debian/Ubuntu Linux
- A git checkout of this repository
- Run the deploy script as the Unix user that should own the service (not
  root; the script uses `sudo` when needed)

## Install

Run the deploy script from the repository checkout:

```bash
./scripts/deploy-server.sh
```

The script:

1. Installs system packages (`ca-certificates`, `curl`, `git`, `openssl`,
   `gpg`), `uv`, Node.js 22, pnpm, and Caddy.
2. Creates `/etc/yuubot/config.yaml` and `/etc/yuubot/yuubot.env` **only when
   missing**, and creates `/var/lib/yuubot` for runtime data.
3. Installs project dependencies and builds the React admin UI via
   `scripts/install-deps.sh`.
4. Installs a single `yuubot.service` systemd unit, removes legacy
   `yuubot-daemon.service` / `yuubot-admin.service` units if present.
5. Prompts for admin HTTPS and the Yuubot admin password, optionally enables
   `public_server` from a public URL, updates listener config, and writes
   `/etc/caddy/conf.d/yuubot.caddy`.
6. Installs systemd, runs migrations, validates, and starts `yuubot.service`.

Useful deploy-time overrides (all optional):

| Variable | Default |
| --- | --- |
| `YUUBOT_CONFIG_DIR` | `/etc/yuubot` |
| `YUUBOT_CONFIG` | `$YUUBOT_CONFIG_DIR/config.yaml` |
| `YUUBOT_ENV_FILE` | `$YUUBOT_CONFIG_DIR/yuubot.env` |
| `YUU_DATA_DIR` | `/var/lib/yuubot` |
| `YUUBOT_PORT` | `8765` (local admin listener) |
| `YUUBOT_PUBLIC_PORT` | `8766` (public listener) |
| `YUUBOT_TRUSTED_ADMIN_PORT` | `8767` (trusted admin listener) |
| `YUUBOT_ADMIN_DOMAIN` | interactive prompt |
| `YUUBOT_PUBLIC_URL` | interactive prompt; when set, enables `public_server` |
| `YUUBOT_SERVICE_USER` / `YUUBOT_SERVICE_GROUP` | current user / group |

## Config

Fresh installs get this shape from the deploy script:

```yaml
data_dir: /var/lib/yuubot
local_admin_server:
  enabled: true
  host: 127.0.0.1
  port: 8765
  url_base: http://127.0.0.1:8765
public_server:
  enabled: false
trusted_admin_server:
  enabled: true
  host: 127.0.0.1
  port: 8767
  url_base: http://127.0.0.1:8767
  auth:
    mode: builtin
    builtin:
      password: <generated during install, replaced by the prompt>
trusted_proxies: [127.0.0.1]
```

`/etc/yuubot/yuubot.env` is sourced by systemd and deploy-time CLI commands:

```bash
YUU_DATA_DIR=/var/lib/yuubot
```

### Listeners

| Listener | Default | Auth | Purpose |
| --- | --- | --- | --- |
| `local_admin_server` | enabled on `127.0.0.1:8765` | `loopback_bypass` | Local admin UI, `ybot status`, Settings apply-update |
| `trusted_admin_server` | enabled on `127.0.0.1:8767` | `builtin` | Remote admin UI via Caddy |
| `public_server` | disabled | none | Public Share pages and app webhooks |

`ybot serve --host` / `--port` only override the **local admin** listener port.
Other listeners use the ports in config.

After the interactive setup, `trusted_admin_server.url_base` and
`public_server.url_base` (when enabled) are written into config automatically.

`trusted_admin_server.auth.mode` must be `proxy` or `builtin` (not
`loopback_bypass`). With the default deploy, Caddy terminates TLS and forwards
traffic to yuubot; yuubot owns the login page, session cookie, and CSRF checks
through builtin auth.

When `public_server` is enabled, the deploy script also writes a second Caddy
vhost that proxies Share pages, app webhooks, and MCP OAuth browser callbacks to
`127.0.0.1:8766`, while returning `404` for other `/api/*` paths.

### Data layout

The main database is `/var/lib/yuubot/db/yuubot.db`. Workspaces, logs, KV, temp
files, and public shares live under `/var/lib/yuubot/workspace`,
`/var/lib/yuubot/logs`, `/var/lib/yuubot/kv`, `/var/lib/yuubot/tmp`, and
`/var/lib/yuubot/published`.

Providers, integrations, actors, routes, and model cards are stored in the
database and managed from the Admin UI.

### Public surface

During install, answer `y` to **Enable public server** and provide the public
URL (for example `public.example.com`). The script enables `public_server`,
sets `url_base`, and configures the Caddy vhost.

App webhooks require per-integration HMAC secrets in the environment (see
`config.example.yaml`).

### Legacy config

Configs using the old split-service top-level keys (`admin`, `database`,
`paths`, `secrets`) are rejected. Replace them with the single-process shape
above.

## Updating

For a server git checkout, use the deploy script's upgrade mode:

```bash
./scripts/deploy-server.sh --upgrade-only
```

Upgrade mode pulls git updates with `--ff-only`, keeps existing config, refreshes
dependencies and the web build, updates generated Caddy routing when needed,
stops `yuubot.service`, applies database migrations, validates the deployment,
and restarts the service. If an existing config already uses builtin auth, the
upgrade path also removes Basic Auth from the generated Caddy admin vhost.

The Admin UI Settings page can check for git updates on any admin listener.
**Apply** only works from loopback (`local_admin_server`), because the update
endpoint requires a loopback client. It schedules the same deploy-script upgrade
path used by `ybot upgrade apply`. Background apply runs sudo non-interactively;
if the service user cannot restart `yuubot.service` without a password prompt,
SSH in and run `./scripts/deploy-server.sh --upgrade-only` manually.

Update logs are written under `/var/lib/yuubot/logs/update-*.log`.

## Operations

Useful checks:

```bash
sudo systemctl status yuubot caddy
sudo journalctl -u yuubot -f
curl -i http://127.0.0.1:8765/healthz
uv run ybot status /etc/yuubot/config.yaml --json
uv run ybot check /etc/yuubot/config.yaml --json
uv run ybot db info /etc/yuubot/config.yaml --json
```

`trusted_admin_server` uses builtin auth by default. API health checks against
port 8767 should use `/healthz`; browser access goes through Caddy and signs in
on the Yuubot `/login` page.

After Caddy setup, open the admin UI at `https://<admin-domain>/`.
