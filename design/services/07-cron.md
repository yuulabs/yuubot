# Design: Cron Jobs and Reminder Delivery

**Implementation order: 7** (depends on [04-tasks.md](04-tasks.md) and [01-runtime-events.md](01-runtime-events.md))

## Scenario

An operator or LLM schedules durable work:

1. `yb.tasks.cron.add(...)` registers a cron job with an explicit IANA timezone.
2. APScheduler triggers the job inside the daemon process.
3. The executor dispatches by action kind:
   - `shell` → `register_shell_task` (same as `yb.tasks.submit`)
   - `actor_message` → actor mailbox with `inbound_kind=actor_inbound`, `conversation_id=None` (ordinary user input through the actor default inbound loop)
   - `conversation_callback` → actor mailbox with `inbound_kind=conversation_callback`, bound to the owner conversation (developer notice + continuation)
   - `reminder` → `NotificationDispatcher` (browser toast / web push / future channels)
4. Admin UI lists, creates, pauses, resumes, and deletes jobs under `/cron`.

Legacy `wakeup` remains decodable for existing jobs and is executed as `actor_message`; it should not be used for new jobs.

Cron jobs persist in SQLite (`app_cron_jobs`). Runtime tasks remain ephemeral.

## Concepts

```text
CronJobStore         = SQLite blob store; source of truth
CronJobScheduler     = APScheduler AsyncIOScheduler wrapper
CronExecutor         = action dispatcher on trigger
NotificationDispatcher = extensible reminder channels
yb.tasks.cron        = execute_python facade (loopback HTTP)
```

## Schedule rules

- `timezone` is **required** on every schedule (`CronSchedule.timezone`).
- Recurring: `{ kind: "cron", timezone, cron }` — standard 5-field crontab interpreted in that timezone.
- One-shot: `{ kind: "at", timezone, at }` — local datetime without offset, interpreted in `timezone`.
- `once=true` or `kind=at` completes the job after the first successful run.

APScheduler 3.x weekday numbering differs from traditional crontab; prefer weekday names (`mon`, `tue`, …) in docs and UI hints.

## HTTP

```http
GET    /api/cron-jobs
GET    /api/cron-jobs/{id}
POST   /api/cron-jobs              # AdminAuth + loopback (yb.tasks.cron)
POST   /api/cron-jobs/{id}/pause
POST   /api/cron-jobs/{id}/resume
DELETE /api/cron-jobs/{id}

GET    /api/notifications/vapid-public-key
POST   /api/notifications/subscriptions
DELETE /api/notifications/subscriptions/{id}
```

Event: `notification.delivered` with `{ job_id, title, body, meta }`.

## Invariants

1. Timezone must be explicit; missing/invalid timezone → `400 bad_request`.
2. Cron jobs are durable; runtime tasks are not.
3. `actor_message` uses user + `run_loop` through the actor default inbound loop; `conversation_callback` and `task_delivery` use developer continuation.
4. Reminders use channel handlers, not actor mailboxes.
5. LLM uses `yb.tasks.cron` only; no direct admin HTTP from `execute_python`.

## Related

- [04-tasks.md](04-tasks.md) — ephemeral shell tasks
- [02-admin-boundary.md](02-admin-boundary.md) — AdminAuth and error envelope
