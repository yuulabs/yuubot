create table if not exists llm_providers (
    id text primary key,
    name text not null,
    protocol text not null,
    config blob not null,
    last_error text,
    updated_at text not null
);

create table if not exists model_cards (
    provider_id text not null references llm_providers(id) on delete cascade,
    selector text not null,
    payload blob not null,
    updated_at text not null,
    primary key (provider_id, selector)
);

insert into llm_providers (id, name, protocol, config, updated_at)
select
    id,
    id,
    case json_extract(payload, '$.provider')
        when 'openai_compatible' then 'openai-compatible'
        when 'openai' then 'openai-compatible'
        when 'deepseek' then 'openai-compatible'
        else coalesce(json_extract(payload, '$.provider'), 'openai-compatible')
    end,
    json_object(
        'endpoint', coalesce(json_extract(payload, '$.endpoint'), ''),
        'api_key', '',
        'options', coalesce(json_extract(payload, '$.options'), json('{}'))
    ),
    updated_at
from app_llms;

insert into model_cards (provider_id, selector, payload, updated_at)
select
    id,
    json_extract(payload, '$.model'),
    json_object(
        'selector', json_extract(payload, '$.model'),
        'vision', json('false'),
        'toolcall', json('true'),
        'json', json('true'),
        'input_price_per_million', json('0'),
        'cached_input_price_per_million', json('0'),
        'output_price_per_million', json('0')
    ),
    updated_at
from app_llms
where json_extract(payload, '$.model') is not null;

drop table app_llms;
