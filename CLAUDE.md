# yuubot-v2

## Monorepo layout

This package lives in a monorepo. Sibling packages are in `../` (i.e.
`agent-kits/`), **not** vendored under `.venv`. When you need to read or
reason about a dependency's source, look next door:

- `../yuuagents` — agent runtime primitives. `Agent` (pure LLM exec: `append` /
  `step` / `done` / `close`), `Stage`, `Runtime`, `MailBox`, tool backends
  (incl. ipykernel). Source under `../yuuagents/src/yuuagents/`.
- `../yuullm` — LLM session/message/streaming layer (`yuullm.user`, `History`,
  `Store`, `ToolCall`, …).
- `../yuutools`, `../yuutrace`, `../yuusekai` — other siblings.

Most subpackages put real source under `<pkg>/src/<pkg>/`.

## Facade architecture (yb / yext)

Agent Python sessions run in an **ipykernel subprocess**. They talk to the
daemon over a **TCP line protocol** (`msgspec.json` + `\n`), see
`src/yuubot/core/facade/`:

- `protocol.py` — `FacadeRpcRequest` (one struct, `kind` field discriminates
  6 message kinds) / `FacadeRpcResponse`.
- `bridge.py` — daemon-side `IntegrationInvokeBridge`: TCP server, token auth,
  dispatches by `kind` (invoke / delegate_submit / im_response /
  background_started / background_finished / schedule).
- `_client.py` (in `src/yb/`) — opens a **new TCP connection per call**.
- `codegen.py` + `workspace.py` — generate the `yext` package (one function per
  capability) to disk, symlink per actor.
- `src/yb/` — handwritten system facade (delegate / im / schedule / tasks).

Key data-flow fact: `delegate.submit()` does **not** return a result. The
bridge sends a `FacadeDelegateTask` mail; the daemon's `SimpleLoopActor`
(`core/actors/impls/simple_loop.py`) runs it via
`YuuAgentsActorRuntime.run_delegate` and injects the result back as a **new
conversation turn** (`BackgroundCompletedMessage`). There is no result store.

`YuuAgentsActorRuntime._run_agent_turn` (`core/assembly/_runtime.py`) is the
core loop: `append` → while `not agent.done`: step → charge budget → run tools.
`run_delegate` is a one-shot wrapper around it.
