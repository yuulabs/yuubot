# yuubot

yuubot v2 is the core runtime for a configurable agent platform.

The runtime is split into two local processes:

- `daemon`: owns resource storage, integration lifecycle, message routing, actor lifecycle, and trace/cost plumbing.
- `admin`: exposes management APIs, plugin install/uninstall endpoints, integration metadata, secret reveal endpoints, and the trace UI mount.

Runtime state lives under the configured `paths.data_dir`. Startup settings come from `config.yaml`; hot-managed resources such as LLM backends, characters, actors, integrations, ingress rules, and prompt templates live in the resource database.

## Commands

```bash
uv run ybot check
uv run ybot daemon
uv run ybot admin
uv run ybot dev
uv run ybot export out.zip
uv run ybot import in.zip --replace
```

## Development

```bash
uv run ruff check src tests
uv run ty check
uv run pytest
```

The product direction and current implementation checklist are tracked in `design/checklist.md`.

## Development Configuration

AI-assisted development uses [OpenAgentControl](https://github.com/OpenAgentControl) for agent orchestration, subagents, skills, and context management. The `.opencode/` directory (gitignored) contains these dev configurations — set it up by following the OpenAgentControl documentation.
