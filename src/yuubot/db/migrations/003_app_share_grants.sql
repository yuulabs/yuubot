create table if not exists app_share_grants (
    id text primary key,
    payload blob not null,
    created_at text not null,
    updated_at text not null
);
