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
5. Prompts for admin HTTPS and the Yuubot admin username/password, optionally
   enables `public_server` from a public URL, updates listener config, and writes
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

## Configure the OpenAI-compatible Gateway

The Admin **Gateway** page owns two resources:

- **Endpoint**: a standard OpenAI-compatible `/v1` base URL, optional encrypted
  API key, timeouts, connection state, and discovered `/v1/models` IDs.
- **Alias**: an ordered `endpoint/model` fallback chain plus administrator
  declared input modalities. Alias capability is not inferred from model names.

An Actor selects either an Alias or an exact `endpoint/model`. Exact selections
bypass Alias routing. Alias fallback happens only before the first visible
stream event, and each target is attempted at most once.

There are two supported deployment paths:

1. Connect yuubot directly to any compatible hosted or local Endpoint.
2. For budgets, accurate billing, rate limits, supplier routing, or more complex
   governance, operate your own OpenAI-compatible gateway and connect it as one
   Endpoint. All such policy remains owned by that external gateway.

yuubot does not calculate monetary cost. The compatibility protocol does not
standardize billing, while cache writes, hosted search, audio, images, and video
change independently across suppliers. The Usage Dashboard therefore reports
requests, tokens, latency, actual endpoint/model, and fallback paths only.

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
      username: admin
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

All runtime paths are anchored under the configured `data_dir`. With the
default deploy script value, `data_dir` is `/var/lib/yuubot`, but operators may
override it during deployment.

The main database is `<data_dir>/db/yuubot.db`. Workspaces, logs, KV, temp files,
and public shares live under `<data_dir>/workspace`, `<data_dir>/logs`,
`<data_dir>/kv`, `<data_dir>/tmp`, and `<data_dir>/published`.

Integrations, actors, routes, Gateway Endpoints, and Aliases are stored in the
database and managed from the Admin UI. Endpoint API keys are encrypted; the
credential encryption key is stored under `<data_dir>/secrets/`.

Configure connections from the **Gateway** page. A failed model refresh does
not stop the Admin listener and does not erase the last discovered model list.
Exact model selections remain valid even when a model is not currently listed.

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

Update logs are written under `<data_dir>/logs/update-*.log`.

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

Anchor database inspection commands to the active SQLite database, not to a
hardcoded install path. For the deploy-script config, the database is derived
from `data_dir`:

```bash
CONFIG=/etc/yuubot/config.yaml
DATA_DIR="$(awk -F: '/^data_dir:/ { sub(/^[[:space:]]+/, "", $2); print $2; exit }' "$CONFIG")"
DB="$DATA_DIR/db/yuubot.db"
```

If the service environment overrides `data_dir`, use that value instead:

```bash
. /etc/yuubot/yuubot.env
DB="$YUU_DATA_DIR/db/yuubot.db"
```

List recent conversations from raw backend history:

```bash
sqlite3 "$DB" '
select conversation_id, count(*) as rows, max(seq) as last_seq, max(created_at) as last_at
from history
group by conversation_id
order by max(created_at) desc
limit 10;
'
```

Inspect the latest conversation's raw persisted messages:

```bash
CID="$(sqlite3 "$DB" "
select conversation_id
from history
group by conversation_id
order by max(created_at) desc
limit 1;
")"

echo "$CID"

sqlite3 -json "$DB" '
with latest(conversation_id) as (
  select conversation_id
  from history
  group by conversation_id
  order by max(created_at) desc
  limit 1
)
select seq, kind, json(payload) as payload, created_at
from history
where conversation_id = (select conversation_id from latest)
order by seq;
'
```

Inspect the latest raw history rows across all conversations:

```bash
sqlite3 -json "$DB" '
select conversation_id, seq, kind, json(payload) as payload, created_at
from history
order by created_at desc, seq desc
limit 30;
'
```

Inspect conversation metadata:

```bash
sqlite3 -header -column "$DB" '
select id, actor_id, status, title, created_at, last_active_at, last_error
from app_conversations
order by last_active_at desc
limit 20;
'
```

`trusted_admin_server` uses builtin auth by default. API health checks against
port 8767 should use `/healthz`; browser access goes through Caddy and signs in
on the Yuubot `/login` page.

After Caddy setup, open the admin UI at `https://<admin-domain>/`.
