create table if not exists app_cron_jobs (
    id text primary key,
    payload blob not null,
    created_at text not null,
    updated_at text not null
);
create index if not exists idx_app_cron_jobs_updated on app_cron_jobs(updated_at);

create table if not exists app_push_subscriptions (
    id text primary key,
    payload blob not null,
    created_at text not null,
    updated_at text not null
);
