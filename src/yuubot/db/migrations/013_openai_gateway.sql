create table if not exists app_gateway_endpoints (
    id text primary key,
    payload blob not null,
    updated_at text not null
);

create table if not exists app_gateway_aliases (
    id text primary key,
    payload blob not null,
    updated_at text not null
);

-- Some narrowly constructed migration fixtures begin after the initial schema.
-- Ensure the historical source table exists so the usage migration remains
-- idempotent for those databases.
create table if not exists app_costs (
    conversation_id text not null,
    seq integer not null,
    usage blob not null,
    account blob,
    estimated integer not null,
    created_at text not null,
    primary key (conversation_id, seq)
);

-- Convert the previous single Gateway connection into a normal Endpoint.
insert or ignore into app_gateway_endpoints (id, payload, updated_at)
select
    'default',
    json_object(
        'id', 'default',
        'name', 'Default',
        'base_url', json_extract(cast(payload as text), '$.base_url'),
        'connect_timeout_s', coalesce(json_extract(cast(payload as text), '$.connect_timeout_s'), 10),
        'request_timeout_s', coalesce(json_extract(cast(payload as text), '$.request_timeout_s'), 300),
        'models', json('[]'),
        'checked_at', '',
        'last_error', null
    ),
    updated_at
from app_gateway_config
where nullif(json_extract(cast(payload as text), '$.base_url'), '') is not null;

-- Every legacy string model becomes a same-name Alias targeting the migrated
-- default Endpoint. This preserves intent without pretending discovery is a
-- source of capability truth.
insert or ignore into app_gateway_aliases (id, payload, updated_at)
select distinct
    json_extract(cast(payload as text), '$.model'),
    json_object(
        'id', json_extract(cast(payload as text), '$.model'),
        'modalities', json_array('text'),
        'targets', json_array(json_object(
            'endpoint_id', 'default',
            'model', json_extract(cast(payload as text), '$.model')
        ))
    ),
    coalesce(updated_at, datetime('now'))
from app_actors
where json_type(cast(payload as text), '$.model') = 'text'
  and nullif(json_extract(cast(payload as text), '$.model'), '') is not null;

insert or ignore into app_gateway_aliases (id, payload, updated_at)
select distinct
    json_extract(cast(payload as text), '$.model.selector'),
    json_object(
        'id', json_extract(cast(payload as text), '$.model.selector'),
        'modalities', json_array('text'),
        'targets', json_array(json_object(
            'endpoint_id', 'default',
            'model', json_extract(cast(payload as text), '$.model.selector')
        ))
    ),
    coalesce(updated_at, datetime('now'))
from app_actors
where json_type(cast(payload as text), '$.model') = 'object'
  and nullif(json_extract(cast(payload as text), '$.model.selector'), '') is not null;

update app_actors
set payload = json_set(
    cast(payload as text),
    '$.model',
    json_object(
        'type', 'alias',
        'alias', json_extract(cast(payload as text), '$.model')
    )
)
where json_type(cast(payload as text), '$.model') = 'text'
  and nullif(json_extract(cast(payload as text), '$.model'), '') is not null;

update app_actors
set payload = json_set(
    cast(payload as text),
    '$.model',
    json_object(
        'type', 'alias',
        'alias', json_extract(cast(payload as text), '$.model.selector')
    )
)
where json_type(cast(payload as text), '$.model') = 'object'
  and nullif(json_extract(cast(payload as text), '$.model.selector'), '') is not null;

create table app_usage (
    conversation_id text not null,
    seq integer not null,
    usage blob not null,
    account blob,
    created_at text not null,
    primary key (conversation_id, seq)
);

insert into app_usage (conversation_id, seq, usage, account, created_at)
select
    conversation_id,
    seq,
    json_remove(
        cast(usage as text),
        '$.payg_cost',
        '$.cost_usd',
        '$.total_cost_usd',
        '$.usd'
    ),
    case
        when account is null then null
        else json_remove(
            cast(account as text),
            '$.response_cost',
            '$.cost',
            '$.cost_usd',
            '$.total_cost_usd',
            '$.usd'
        )
    end,
    created_at
from app_costs;

drop table app_costs;
drop table if exists app_gateway_config;
