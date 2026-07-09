update app_integrations
set payload =
    case
        when json_type(cast(payload as text), '$.config.api_key') is not null then
            json_set(
                json_remove(cast(payload as text), '$.config.api_key'),
                '$.id',
                'web',
                '$.type',
                'web',
                '$.name',
                case json_extract(cast(payload as text), '$.name')
                    when 'tavily_web' then 'web'
                    else json_extract(cast(payload as text), '$.name')
                end,
                '$.config.tavily_api_key',
                json_extract(cast(payload as text), '$.config.api_key')
            )
        else
            json_set(
                cast(payload as text),
                '$.id',
                'web',
                '$.type',
                'web',
                '$.name',
                case json_extract(cast(payload as text), '$.name')
                    when 'tavily_web' then 'web'
                    else json_extract(cast(payload as text), '$.name')
                end
            )
    end
where type = 'tavily_web';

update app_integrations
set type = 'web'
where type = 'tavily_web';

update app_routes
set integration_type = 'web'
where integration_type = 'tavily_web';
