create table if not exists app_conversation_response_state (
    conversation_id text primary key references app_conversations(id) on delete cascade,
    payload blob not null,
    updated_at text not null
);
