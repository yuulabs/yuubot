# Server Deployment

yuubot now runs as one HTTP service:

```text
Cloudflare DNS/CDN
  -> HTTPS
  -> Caddy
  -> ybot serve /etc/yuubot/config.yaml on 127.0.0.1:8765
```

## Install

Run the deploy script as the Unix user that should own the service:

```bash
./scripts/deploy-server.sh
```

The script installs system dependencies, `uv`, Node.js, pnpm, Caddy, Python
dependencies, and the React build. It writes `/etc/yuubot/config.yaml` if
missing, installs `yuubot.service`, and configures Caddy as the public reverse
proxy.

## Config

The deployed config uses the new single-process shape:

```yaml
data_dir: /var/lib/yuubot
admin_url_base: https://admin.example.com
public_url_base: https://admin.example.com
trusted_proxies: [127.0.0.1]
admin_auth:
  mode: loopback_bypass
```

The main database lives at `/var/lib/yuubot/db/yuubot.db`; workspaces, logs, KV,
and published shares live under `/var/lib/yuubot/workspace`, `/logs`, `/kv`, and
`/published`. Providers, integrations, actors, routes, and model cards are
configured from the Admin UI and stored in that database.

## Upgrade From Old Deployments

1. Stop the old services:
   `sudo systemctl stop yuubot-admin yuubot-daemon`
2. Back up the old data directory:
   `sudo cp -a /var/lib/yuubot /var/lib/yuubot.backup.$(date +%Y%m%d%H%M%S)`
3. Dry-run the database import:
   `uv run ybot migrate /etc/yuubot/config.yaml --from-old-config /etc/yuubot/old-config.yaml --dry-run --json`
4. Run the real import:
   `uv run ybot migrate /etc/yuubot/config.yaml --from-old-config /etc/yuubot/old-config.yaml --json`
5. Start the new service:
   `sudo systemctl start yuubot`

If the old database is at the default `<data_dir>/yuubot/yuubot.db` and the new
database does not exist yet, `ybot migrate` discovers it automatically.

## Operations

Useful checks:

```bash
sudo systemctl status yuubot caddy
sudo journalctl -u yuubot -f
curl -i http://127.0.0.1:8765/healthz
uv run ybot status /etc/yuubot/config.yaml --json
uv run ybot db info /etc/yuubot/config.yaml --json
```

To deploy a new commit:

```bash
git pull
./scripts/deploy-server.sh
```
