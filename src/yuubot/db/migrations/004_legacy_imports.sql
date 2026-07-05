create table if not exists legacy_imports (
    source_path text primary key,
    imported_at text not null,
    report blob not null
);
