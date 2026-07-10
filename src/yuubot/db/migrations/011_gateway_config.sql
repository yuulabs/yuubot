create table if not exists app_gateway_config (
    id integer primary key check (id = 1),
    payload blob not null,
    updated_at text not null
);
