-- Phase A: Destructive migration — drop Provider/ModelCard tables.
-- Actor payloads are updated to remove provider and set model to null.

pragma secure_delete = on;

create table if not exists app_gateway_config (
    id integer primary key check (id = 1),
    payload blob not null,
    updated_at text not null
);

insert or ignore into app_gateway_config (id, payload, updated_at)
select
  1,
  json_object(
    'base_url', coalesce(json_extract(cast(config as text), '$.endpoint'), ''),
    'connect_timeout_s', 10,
    'request_timeout_s', 300
  ),
  updated_at
from llm_providers
where nullif(json_extract(cast(config as text), '$.endpoint'), '') is not null
order by updated_at desc
limit 1;

-- Provider records are removed. Actor model selectors are migrated later once
-- the Endpoint/Alias tables exist, so preserve the legacy model value here.
begin immediate;
update app_actors set
  payload = json_remove(payload, '$.provider'),
  enabled = 0,
  status = 'blocked',
  last_error = json_object(
    'type', 'gateway_model_unavailable',
    'message', 'actor model selection requires Gateway migration'
  )
where payload is not null;

-- Drop model_cards table (references llm_providers via FK).
drop table if exists model_cards;

-- Drop llm_providers table.
drop table if exists llm_providers;

commit;
