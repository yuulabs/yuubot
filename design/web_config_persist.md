# Web Config Panel & Persistent Storage

## Overview

A simple WebUI control panel running on a **separate port** (default 8781, configurable)
from the daemon's internal agent-fns API (8780).
No build step — xterm.js via CDN, vanilla HTML/CSS/JS or HTMX.

**Port separation rationale**: 8780 is the internal agent-fns RPC bus (token-authenticated,
kernel-only). Mixing it with the operator panel would mean docker port-mapping exposes the
internal API. The admin panel port is the only one that needs to be reachable from outside
the container.

## Feature Areas

- Terminal & filesystem access (most urgent)
- Character configuration
- Traces viewer
- Memory / schedule management
- Machine config (SSH keys, playwright cookies, etc.)

---

## Terminal + Persistent Paths

These are two parts of the same concern: giving the operator safe, persistent access to
filesystem state inside the Docker container.

### Problem

Docker provides isolation (prevents bot from compromising the host), but internal paths
like `~/.ssh`, `~/.config/playwright` are ephemeral — wiped on container rebuild.

### Solution

**Persistent paths** declared in `config.yaml`:

```yaml
persistent_paths:
  - ~/.ssh
  - ~/.config/playwright
```

Each path is mirrored under `data/yuubot/persist/<expanded-docker-path>`.
Example: `~/.ssh` → `data/yuubot/persist/home/user/.ssh`

The internal path becomes a symlink pointing to the data directory entry.
`data/` is a volume-mounted persistent directory, survives container rebuilds.

**Bootstrap logic** (idempotent, runs at daemon startup via `ybot setup`):

| State | data/ entry | internal path | Action |
|-------|------------|---------------|--------|
| First deploy | empty/absent | real directory | copy to data/, replace with symlink |
| Container rebuilt | has content | real directory | remove internal, create symlink |
| Already configured | has content | is symlink | no-op |

### Terminal

xterm.js (CDN) + WebSocket endpoint on the admin port that proxies a PTY (`ptyprocess` library).
Browser terminal supports native copy-paste, solving the clipboard problem.
The operator opens a terminal in the panel, navigates to e.g. `~/.ssh`, and edits files directly.

---

## Character Serialization

### Storage

- Built-in characters: Python in `characters/__init__.py`, read-only in UI
- User-created characters: YAML files in `data/yuubot/characters/*.yaml`
- Section library: individual files in `data/yuubot/sections/`

### Character YAML Schema

Direct serialization of the current `Character` / `AgentSpec` structure:

```yaml
name: my_agent
description: "..."
bot_kinds: [group]       # omit = all
spec:
  facade_modules: [im, web, vision]   # service module aliases
  max_turns: 20
  delegate: [general, mem_curator]    # character names; resolved at load time
  system_prompt: |                    # complete prompt stored on character
    You are ...
```

**Facade modules** map to `agent_fns/` service modules (`im`, `web`, `mem`, `vision`, etc.).
UI shows available modules as a checklist. Third-party libs (numpy etc.) are irrelevant —
the bot can import them directly in code.

**`ya.PythonImport` entries** (execution backend config) are referenced via predefined aliases,
not configured per-character in the UI.

### Section Library

Each section is a standalone file in `data/yuubot/sections/`.
No inline text — every section has its own file.

Built-in sections (e.g. `python_runtime` → `PYTHON_RUNTIME_SECTION`) are registered with
canonical names; UI displays names only, not Python symbols.

```
data/yuubot/sections/
  python_runtime.md       # maps to PYTHON_RUNTIME_SECTION
  safety_base.md
  yuu_persona.md
  ...
```

### Loading (two-pass)

1. Register all character names (built-in + YAML) into the registry
2. Resolve delegate references by name

This allows user-created characters to be referenced as delegates by other characters
regardless of definition order.

---

## Other WebUI Areas (design TBD)

- **Traces viewer**: expose `scripts/conv.py` logic as REST endpoints; read-only
- **Memory management**: CRUD on `mem` service data
- **Schedule management**: list/edit/delete cron entries, trigger `/schedule/reload`

---

## Deployment Architecture

### Principles

- **Access over copy**: sensitive state (cookies, SSH keys) stays where it lives; the container accesses it rather than importing a copy.
- **Container has no environment assumptions**: bot behaves as if on a normal machine. `persist.py` handles path persistence transparently via symlinks.
- **Good enough beats clever**: automation complexity must match actual usage frequency.

### Single-service compose

`docker-compose.yml` has one service: `yuubot`. No separate proxy/bridge container.
The main container exposes two ports:
- `8780` — internal agent-fns RPC (token-authenticated, not mapped externally)
- `8781` — admin panel (operator-facing)
- `external_service_port` (configurable) — host bridge endpoint, optional

### ybot bridge

`ybot bridge` is a host-side CLI command (not a container). It starts a local HTTP service that responds on `external_service_port`, providing capabilities that require host-native state:

- Chrome CDP access (browser auth, cookies)
- Future: other host-side operations

The bot calls `external_service_port` when it needs these capabilities. If the port is unreachable, it degrades gracefully — no crash, no error flood.

**Startup**: manual. After a host reboot, the operator runs `ybot bridge` on the host and starts Chrome with `--remote-debugging-port=9222`. If the bot encounters an auth failure before this is done, it notifies master via QQ. Machines running the bot don't reboot often; the automation complexity of auto-starting these is not justified.

**Chrome CDP**: the only browser backend. Cookie export (DPAPI) is not used — cookies stay in the Windows Chrome process, the bridge connects via CDP (`host.docker.internal:9222` from the container's perspective).

### Proxy configuration

Explicit in `config.yaml`, no environment inference:

```yaml
proxy:
  http: "http://user:pass@host:port"
  https: "http://user:pass@host:port"
  no_proxy: "localhost,127.0.0.1,host.docker.internal"
```

Docker-side uses `host.docker.internal`; host-side uses `127.0.0.1` or the actual IP.
Credentials go in `.env` via `${VAR}` substitution, not committed to git.

### Security boundary

Docker isolation is for **environment reproducibility**, not as a security boundary against the master operator. The master's `execute_python` (kernel backend) is intentionally unrestricted. The threat model for group/public users is already handled by the `restricted` backend — no additional container-level isolation is needed for that path.
