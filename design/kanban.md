# yuubot Next-Step Kanban

Updated: 2026-06-03

## Target Scenario

User opens Admin, creates an OpenAI-compatible LLM backend with a custom base URL
and API key, clones a builtin character, edits its prompt, selects allowed tools
and integration capabilities, then launches a `simple_loop` actor. The user opens
Admin Conversation, sends a test message to that actor's conversation Agent, sees the agent reply, and can use
that first-party channel to verify the actor is actually working before wiring
external integrations. During the turn, the actor sees the intended `yb` system
facade, sees only the allowed `yext` integration functions, and can use a
long-running tool through `yb.tasks.submit_bg`. The user can then inspect the
trace tree, token use, cost, tool calls, and integration calls in Monitor. If a
real-world integration such as GitHub or Web Search is enabled, the same actor can
call it through the same capability/facade boundary.

Current code already has the backbone for this:

- Runtime resources: `llm-backends`, `characters`, `actors`,
  `prompt-templates`, `integrations`, and `ingress-rules`.
- Bootstrap config is separated from user-managed runtime resources.
- Actors already bind `CharacterRecord`, `LLMBackendRecord`, model, tools, and
  `allowed_capability_ids`.
- `yb` is handwritten system facade; `yext` is generated from integration
  capability schemas.
- Integration invocation enforces actor-level capability permissions.
- Trace context already tags actor, character, model, integration, capability,
  and task ids.

## Now

### 1. Backend/Frontend Config And Admin Conversation Launch Path

Goal: a user can configure LLM backends, characters, and actors from Admin and
launch and test an actor's conversation Agent through Admin Conversation without hand-editing DB payloads.

- [x] Add Admin resource API facade over daemon `/api/resources`.
  - Admin frontend must not need the daemon secret directly.
  - Support list/get/create/update/delete and enable/disable for all registered
    resource types.
  - Preserve daemon validation and refresh actions in Admin responses.
- [x] Turn the static Admin demo pages into a working resource client.
  - Providers page manages `LLMBackendRecord`.
  - Characters page manages `CharacterRecord`.
  - Actors page manages `ActorRecord`.
  - Routes page manages `ActorIngressRuleRecord`.
- [x] Add LLM backend templates.
  - OpenAI, OpenAI-compatible, Anthropic, DeepSeek, Gemini. 
  - Template fills `yuuagents_provider`, `provider_options`, `default_model`,
    stream options, model catalog, and pricing scaffold.
  - Secrets are written through existing `Secret` wrapping/redaction.
- [ ] Add a backend connection/model validation action.
  - Validate API key, base URL/provider options, and selected default model.
  - Report whether streaming/tool calling/vision/reasoning are supported.
- [x] Add actor create wizard.
  - Select character.
  - Select LLM backend and model.
  - Select yuuagents tools.
  - Select integration capabilities.
  - Configure budget/runtime/resource policy.
  - Create actor and trigger daemon reconcile.
- [ ] Add minimal first-party Admin Conversation.
  - Expose `/api/admin/conversations` through Admin and daemon.
  - Create or reuse a conversation-mode Agent for the selected Actor.
  - Send user messages directly to that Agent thread, not through the Actor mailbox.
  - Stream agent replies, events, and turn errors back to the browser.
  - Persist conversation history for reload and debugging.
- [ ] Add a launch smoke path in the UI.
  - After actor creation, offer "Open in Admin Conversation".
  - Admin Conversation actor selector only lists enabled/running actors.
  - First message should create or reuse a conversation id.
  - Actor reply should link to Monitor trace/detail when trace is available.
- [ ] Acceptance checks:
  - `uv run pytest tests/test_daemon_commands.py tests/test_actor_lifecycle.py`
  - New Admin API tests for resource proxy, secret redaction, actor launch, and
    Admin Conversation message delivery.
  - Manual smoke: create backend -> create character -> create actor ->
    `/api/status` shows actor running -> send Admin Conversation message -> receive agent
    reply.

### 2. Tool, Facade, And Prompt Visibility

Goal: the actor can see the tools and facade modules it is allowed to use, and
the prompt organization is explicit enough to maintain without guessing.

- [ ] Write `design/prompts.md`.
  - Define prompt layers in assembly order:
    1. yuubot core/runtime contract
    2. character system prompt
    3. prompt templates
    4. expanded skills
    5. tool/facade catalog
    6. runtime message/context
  - Define which layers are persisted resources and which are runtime-generated.
  - Define cache-friendly expansion rules for skills and templates.
  - Define how `yb` and `yext` capabilities are described to the model.
- [ ] Add a `PromptAssembler` boundary.
  - Current `build_agent_definition()` passes only `character.system_prompt`.
  - New boundary should compose character, templates, skills, and facade/tool
    descriptions into one `PromptDefinition`.
  - Keep actor runtime independent from frontend form details.
- [ ] Add a tool/facade catalog API.
  - Expose available yuuagents tool backends from bootstrap config.
  - Expose builtin `yb` modules (`yb.actor`, `yb.tasks`, future `yb.webui`,
    `yb.schedule`, `yb.delegate`).
  - Expose `yext` capability specs grouped by integration kind and enabled
    integration record.
- [ ] Make generated facade imports dynamic.
  - Current required imports include `yext.echo`.
  - Generate/import namespaces from the actor's visible capability specs instead
    of hardcoding echo.
  - Preserve `yb` imports as handwritten system facade imports.
- [ ] Tighten permission tests.
  - Actor with no `allowed_capability_ids` cannot import or call generated
    integration functions.
  - Actor with one allowed capability sees only that generated function.
  - `yb` calls still enforce actor/session/mailbox context.
- [ ] Acceptance checks:
  - `uv run pytest tests/test_integration_actor_echo.py tests/test_yext_submit_bg.py`
  - New prompt assembler tests snapshot the assembled system prompt.
  - New facade generation tests cover multi-namespace capabilities.

## Next

### 3. Monitor Panel And Trace Visualization

Goal: Monitor shows enough runtime evidence that the user can debug an actor
without opening raw trace storage first.

- [ ] Add Admin monitor summary endpoints.
  - Daemon/admin health.
  - Running actors and integrations.
  - Recent trace list.
  - Cost by actor, model, backend, integration capability, and day.
- [ ] Build a trace adapter over `traces.db`.
  - Keep mounted yuutrace UI as the deep inspection view.
  - Add yuubot-specific summaries using trace attributes already emitted:
    `yuubot.actor_id`, `yuubot.character_name`, `yuubot.model`,
    `yuubot.integration_id`, `yuubot.capability_id`, `yuubot.task_id`.
- [ ] Implement visual trace tree in Monitor.
  - Actor turn -> LLM calls -> tool calls -> integration calls -> background
    tasks.
  - Show duration, status, tokens, cost, and selected payload summaries.
  - Link every row to the mounted yuutrace detail page.
- [ ] Add failure-oriented views.
  - Last actor errors.
  - Failed integration invocations.
  - Long-running background tasks.
  - Budget exhaustion warnings.
- [ ] Acceptance checks:
  - `uv run pytest tests/test_trace_cost_e2e.py`
  - New tests for monitor summary aggregation using fixture traces.
  - Manual smoke: send echo HTTP message -> actor responds -> monitor displays
    actor/model/cost/capability rows.

### 4. Real-World Integrations First Wave

Goal: ship integrations that make actors useful outside the demo loop while
reusing the existing integration contract and generated `yext` facade.

- [ ] Web Search / Web Read builtin integration.
  - Capabilities: `web.search`, `web.read`.
  - Config: provider, API key secret, timeout, max result/page limits.
  - `web.read` returns structured page chunks, not one opaque string.
- [ ] GitHub integration.
  - Capabilities first: issue/PR read, comment, file fetch, search.
  - Admin config supports token secret and repository allowlist.
  - Actor facade keeps write actions explicit and permission-gated.
- [ ] Project management integration.
  - Start with Linear and Lark/Notion (online doc).
  - Capabilities first: search/read issues, create/update issue, comment.
  - Keep schema close to the provider's native IDs and URLs.
- [ ] IM integration spike.
  - Telegram or Discord first.
  - Validate inbound message -> gateway -> actor -> response path.
  - Confirm route UX before adding more IM platforms.
- [ ] External plugin developer loop.
  - Document manifest, facade schema, subprocess lifecycle, ingest, and storage.
  - Add a minimal sample plugin that exercises inbound messages and facade calls.
- [ ] Acceptance checks:
  - Each integration has config schema, capability schemas, storage behavior,
    permission tests, and one end-to-end invocation test.
  - Real credentials are never required for unit tests.

## Later

### 5. Deployment Infrastructure

Goal: yuubot can be installed, operated, upgraded, and recovered on a real
server without requiring the user to manually assemble process managers,
certificates, data mounts, and upgrade scripts.

- [ ] Docker image and compose profile.
  - Publish a runnable image for the daemon/admin runtime.
  - Provide `docker compose` templates for localhost and public-server modes.
  - Mount only `data_dir` for persistent runtime state.
  - Expose daemon/admin/trace ports through explicit config.
- [ ] Interactive installer.
  - `curl ... | bash` entrypoint for first install.
  - Collect domain, public/private mode, ports, data path, master key, and admin
    secret.
  - Generate `.env`, `config.yaml`, compose file, and system service wrapper.
  - Run initial health checks and print the Admin URL.
- [ ] HTTPS and reverse proxy infrastructure.
  - Support Caddy or nginx profile.
  - Automate Let's Encrypt setup for public domains.
  - Support localhost/self-signed development mode.
  - Ensure Admin can be protected without exposing daemon internals.
- [ ] Online upgrade helper.
  - Check current version, latest version, and image digest.
  - Backup/export `data_dir` before upgrade.
  - Pull/build new image, restart services, run health checks.
  - Roll back to previous image/config when health checks fail.
- [ ] Runtime backup and restore tooling.
  - Integrate archive export/import with deployment scripts.
  - Add dry-run restore conflict report for persistent paths.
  - Document recovery steps for lost container, moved host, and failed upgrade.
- [ ] Deployment hardening.
  - Validate secrets, file permissions, exposed ports, TLS status, and daemon
    loopback isolation.
  - Add `ybot check deployment` or equivalent installer health command.
  - Produce human-readable diagnostics for common server failures.
- [ ] Acceptance checks:
  - Fresh install on a clean Linux server reaches Admin over HTTPS.
  - Upgrade preserves actors, integrations, traces, plugins, and workspaces.
  - Failed upgrade can roll back without losing `data_dir`.
  - `uv run ybot check` or deployment check reports no critical issues.

### 6. Yuu Network

Goal: an actor can maintain a private resource network across external machines,
so users can attach remote compute, files, services, and project environments
without installing the full yuubot runtime on every node.

- [ ] Write `design/yuu-network.md`.
  - Define the concrete join flow:
    1. Admin enables Yuu Network for one actor.
    2. yuubot creates an agent node and join gate.
    3. User runs `ynet join <gate> <token>` on a remote machine.
    4. Remote resource node declares its profile.
    5. Actor is notified and can inspect/use the node.
  - Define protocol messages, persisted node records, tunnel state, and failure
    behavior.
  - Keep 0.1 scoped to one Yuu Network per actor.
- [ ] Build the agent-node runtime in yuubot.
  - Actor-owned network service with join gate URL.
  - One-time join token creation, expiry, revocation, and audit events.
  - Node registry with UUID, short id, name, profile, status, and last seen.
  - Actor mailbox notifications when nodes join, update profile, fail, or exit.
- [ ] Build the lightweight `ynet` client.
  - Installable standalone CLI, separate from full `ybot`.
  - Generates node keypair locally and never sends private keys.
  - Sends version/name/type/profile during join.
  - Maintains reverse tunnel or equivalent authenticated connection.
  - Supports reconnect, status, leave, and profile refresh.
- [ ] Define resource-node profiles and actor-visible inventory.
  - Profile includes human-readable description plus structured resources.
  - Initial resource types: CPU/GPU summary, filesystem roots, HTTP endpoints,
    shell/command capability, and free-form service descriptors.
  - Actor sees short ids and profiles in prompt/context.
  - Actor can query current node inventory through `yb` facade.
- [ ] Add remote resource access capabilities.
  - Start with read-only inventory and HTTP proxy access.
  - Add file and command access only behind explicit resource policy.
  - Every remote operation is attributed to actor id, node id, and request id.
  - Remote output is treated as untrusted external content in prompts.
- [ ] Add Admin UI for Yuu Network.
  - Enable/disable network per actor.
  - Generate and revoke join tokens.
  - Show connected nodes, profiles, resources, last seen, and errors.
  - Show copyable install/join command for `ynet`.
- [ ] Security and deployment requirements.
  - Join token is only for first join; ongoing auth uses node keypair.
  - Agent node verifies resource node identity on every connection.
  - Resource node verifies it joined the intended agent node.
  - Support public HTTPS deployment and localhost/dev tunnel mode.
  - Add clear warnings for command/file access risk.
- [ ] Acceptance checks:
  - Local test starts yuubot, joins a `ynet` resource node, and actor receives a
    node-joined message.
  - Actor can list node inventory through `yb`.
  - Revoked token cannot join.
  - Disconnected node marks failed and notifies actor.
  - Remote HTTP resource can be called through the network with trace
    attribution.

### 7. Skills Management

- [ ] Resource-backed skill catalog rooted at `data*/skills`.
- [ ] UI for browse/edit/import/reset.
- [ ] `load_skills` actor-visible helper.
- [ ] Cache-friendly skill expansion into prompt assembly.

### 8. Runtime Operations

- [ ] Web PTY for local/remote debugging.
- [ ] Web FS for actor workspaces and data directory inspection.
- [ ] Archive import/export conflict UI for persistent paths.
- [ ] Admin restart/reload controls for bootstrap-only config changes.

## Cross-Cutting Rules

- Keep bootstrap config for daemon infrastructure only. User-managed runtime
  state belongs in resources and should hot-refresh when possible.
- Keep `yb` handwritten and `yext` generated. Do not mix system helpers into
  generated integration packages.
- Every Admin create/edit flow should have a matching daemon/API validation test.
- Any capability visible in the frontend must have the same permission boundary
  enforced in `IntegrationCore.invoke`.
- Prefer one complete integration path over many partial provider stubs.
