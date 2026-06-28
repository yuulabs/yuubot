# Server deployment

This is the first-pass single-server deployment shape:

```text
Cloudflare DNS/CDN
  -> HTTPS
  -> Caddy with Basic Auth
  -> yuubot-admin on 127.0.0.1:8781
  -> yuubot-daemon on 127.0.0.1:8780
```

The daemon is not exposed publicly. Caddy is the only public entry point.

## Prerequisites

- A Linux server, currently Debian/Ubuntu for the install script.
- A domain or subdomain managed in Cloudflare.
- Ports `80/tcp` and `443/tcp` open.

## Install

Clone the repository on the server, then run it as the user that should own the
yuubot service processes:

```bash
./scripts/deploy-server.sh
```

The script will:

- install system dependencies, Node.js 22, `uv`, and Caddy when missing;
- enable `pnpm` through Corepack;
- create `/etc/yuubot/config.yaml` and `/etc/yuubot/yuubot.env` if missing;
- preserve any existing `YUU_SECRET_KEY`;
- run `uv sync`;
- build `apps/yuubot/web/dist`;
- install and start `yuubot-daemon.service` and `yuubot-admin.service`;
- ask for a domain, username, and passphrase;
- write Caddy Basic Auth to `/etc/caddy/conf.d/yuubot.caddy` using a password
  hash, not the plaintext passphrase.

## Cloudflare

After the script prints the selected domain:

1. Add an `A` record pointing the domain to the server public IPv4.
2. Use `DNS only` for the first certificate issuance.
3. Set Cloudflare SSL/TLS mode to `Full (strict)`.
4. After `https://<domain>` works, switching the record to `Proxied` is OK.

## Operations

Useful checks:

```bash
sudo systemctl status yuubot-daemon yuubot-admin caddy
sudo journalctl -u yuubot-daemon -u yuubot-admin -f
```

To deploy a new commit:

```bash
git pull
./scripts/deploy-server.sh
```

The script keeps existing config and secrets by default.
If `/etc/caddy/conf.d/yuubot.caddy` already exists, it also keeps the existing
Caddy domain and Basic Auth hash unless you choose to reconfigure it.
