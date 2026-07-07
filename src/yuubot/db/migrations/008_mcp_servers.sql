create table if not exists app_credentials (
    id text primary key,
    payload blob not null,
    updated_at text not null
);

create table if not exists app_credential_secrets (
    credential_id text primary key references app_credentials(id) on delete cascade,
    encrypted_payload blob not null,
    updated_at text not null
);

create table if not exists app_mcp_servers (
    id text primary key,
    payload blob not null,
    enabled integer not null,
    last_error text,
    capabilities blob,
    updated_at text not null
);

create table if not exists app_skills (
    id text primary key,
    payload blob not null,
    updated_at text not null
);

create table if not exists app_auth_attempts (
    id text primary key,
    payload blob not null,
    updated_at text not null
);
