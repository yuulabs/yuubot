"""Shared prompt fragments for system prompt assembly."""

ADMIN_PAGES = "\n".join(
    [
        "For interactive admin pages, write HTML/CSS/JS under the workspace (for example `projects/.../form.html`).",
        "Serve/open those pages through the admin surface, for example `/api/actors/{actor_id}/files/projects/.../form.html`; `{actor_id}` is the Actor id from Workspace Instructions. Public share URLs under `/s/...` do not expose `/api`, KV, or inbound.",
        "When an admin opens the page in the management UI, same-origin page JavaScript may call admin KV and inbound endpoints with AdminAuth:",
        "- `GET` / `PUT` / `DELETE` `/api/actors/{actor_id}/kv/{key}` (`{actor_id}` is your Actor id; `{key}` is URL-encoded; supports `ETag` / `If-Match`)",
        "- `POST` `/api/actors/{actor_id}/inbound` (`{actor_id}` is your Actor id; `text` plus optional `conversation_id`)",
        "For `PUT`, `POST`, and `DELETE`, include `Content-Type: application/json`. If `localStorage.getItem(\"yuubot:csrf-token\")` returns a token, also send `X-CSRF-Token: <token>`.",
        "Recommended submit flow: persist draft state to KV, then POST inbound with structured JSON `text` containing `submitted_at`, `source_page`, `purpose` or `context`, optional `kv_key`, and `payload`.",
        "Admin KV and inbound are browser-driven from page JavaScript; do not call them from execute_python.",
    ]
)

ADMIN_PAGES_INTRO = "For interactive admin pages, write HTML/CSS/JS under the workspace"
ADMIN_PAGES_SUBMIT_FLOW = "Recommended submit flow: persist draft state to KV, then POST inbound"
