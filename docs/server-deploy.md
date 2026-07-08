# Server Deployment

yuubot runs one runtime behind explicit HTTP listeners:

```text
Cloudflare DNS/CDN
  -> HTTPS
  -> Caddy
  -> public listener on 127.0.0.1:8766
  -> trusted admin listener on 127.0.0.1:8767
```

## Install

Run the deploy script as the Unix user that should own the service:

```bash
./scripts/deploy-server.sh
```

The script installs system packages, `uv`, Node.js, pnpm, Caddy, project
dependencies, and the React build. It writes `/etc/yuubot/config.yaml` only when
missing, installs the systemd unit, runs database migrations while the service
is stopped, validates the config, restarts `yuubot.service`, and writes the
Caddy site file.

## Config

Use this config shape:

```yaml
data_dir: /var/lib/yuubot
local_admin_server:
  enabled: true
  host: 127.0.0.1
  port: 8765
  url_base: http://127.0.0.1:8765
public_server:
  enabled: true
  host: 127.0.0.1
  port: 8766
  url_base: https://public.example.com
trusted_admin_server:
  enabled: true
  host: 127.0.0.1
  port: 8767
  url_base: https://admin.example.com
  auth:
    mode: builtin
    builtin:
      password: change-me
trusted_proxies: [127.0.0.1]
```

The main database is `/var/lib/yuubot/db/yuubot.db`. Workspaces, logs, KV, temp
files, and public shares live under `/var/lib/yuubot/workspace`,
`/var/lib/yuubot/logs`, `/var/lib/yuubot/kv`, `/var/lib/yuubot/tmp`, and
`/var/lib/yuubot/published`.

Providers, integrations, actors, routes, and model cards are stored in the
database and managed from the Admin UI.

## Updating

For a server checkout:

```bash
git pull --ff-only
./scripts/deploy-server.sh
```

Rerunning the script keeps existing config and Caddy credentials, refreshes
dependencies and the web build, stops `yuubot.service`, applies database
migrations, validates the deployment, and restarts the service.

The Admin UI Settings page can also check and apply git-based updates. Update
logs are written under `/var/lib/yuubot/logs/update-*.log`.

## Operations

Useful checks:

```bash
sudo systemctl status yuubot caddy
sudo journalctl -u yuubot -f
curl -i http://127.0.0.1:8765/healthz
uv run ybot status /etc/yuubot/config.yaml --json
uv run ybot db info /etc/yuubot/config.yaml --json
```
