create table if not exists app_meta (
    key text primary key,
    value text not null
);

create table if not exists app_llms (
    id text primary key,
    payload blob not null,
    updated_at text not null
);

create table if not exists app_integrations (
    type text primary key,
    payload blob not null,
    enabled integer not null,
    last_error blob,
    updated_at text not null
);

create table if not exists app_actors (
    id text primary key,
    payload blob not null,
    enabled integer not null,
    status text not null default 'idle',
    last_error blob,
    updated_at text not null
);

create table if not exists app_conversations (
    id text primary key,
    actor_id text not null,
    status text not null,
    created_at text not null,
    last_active_at text not null,
    last_error blob
);

create table if not exists app_costs (
    conversation_id text not null,
    seq integer not null,
    usage blob not null,
    account blob,
    estimated integer not null,
    created_at text not null,
    primary key (conversation_id, seq)
);

create table if not exists history (
    conversation_id text not null,
    seq integer not null,
    kind text not null,
    payload blob not null,
    created_at text not null default '',
    primary key (conversation_id, seq)
);
