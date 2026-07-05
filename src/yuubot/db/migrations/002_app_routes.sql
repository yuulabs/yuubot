create table if not exists app_routes (
    id text primary key,
    integration_type text not null default '',
    pattern text not null,
    actor_id text not null,
    enabled integer not null default 1,
    created_at text not null,
    updated_at text not null
);

create unique index if not exists app_routes_pattern on app_routes(pattern);
