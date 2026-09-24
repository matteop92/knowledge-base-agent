-- Private corpus: only a server-side role may enter this schema.
create schema if not exists extensions;
create extension if not exists vector with schema extensions;
create schema if not exists knowledge;
revoke all on schema knowledge from public, anon, authenticated;

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'knowledge_runtime') then
    create role knowledge_runtime nologin;
  end if;
end $$;
grant usage on schema knowledge, extensions to knowledge_runtime;

create table knowledge.documents (
  document_id text primary key,
  root_alias text not null,
  relative_path text not null,
  unique (root_alias, relative_path)
);

create table knowledge.runs (
  run_id text primary key,
  document_id text not null references knowledge.documents,
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  source_bytes bytea not null,
  source_text text not null,
  parsed jsonb not null,
  parser_version text not null,
  manifest jsonb not null,
  loaded_at timestamptz not null default now(),
  unique (run_id, document_id),
  check (octet_length(source_bytes) > 0),
  check (length(source_text) > 0)
);

create table knowledge.chunk_sets (
  run_id text not null references knowledge.runs,
  variant text not null check (variant in ('fixed_raw', 'structural_raw')),
  chunking_version text not null,
  tokenizer_version text not null,
  payload jsonb not null,
  primary key (run_id, variant, chunking_version)
);

create table knowledge.chunks (
  run_id text not null,
  chunk_id text not null,
  variant text not null,
  chunking_version text not null,
  position integer not null check (position >= 0),
  raw_text text not null check (length(raw_text) > 0),
  source_spans jsonb not null check (jsonb_typeof(source_spans) = 'array' and jsonb_array_length(source_spans) > 0),
  block_indices jsonb not null,
  content_roles jsonb not null,
  heading_path jsonb,
  payload jsonb not null,
  primary key (run_id, chunk_id),
  unique (run_id, variant, chunking_version, position),
  foreign key (run_id, variant, chunking_version)
    references knowledge.chunk_sets (run_id, variant, chunking_version)
);

create function knowledge.validate_chunk_spans() returns trigger
language plpgsql set search_path = '' as $$
declare span jsonb; source_size integer; previous_end integer := 0;
begin
  select char_length(source_text) into source_size from knowledge.runs where run_id = new.run_id;
  for span in select value from pg_catalog.jsonb_array_elements(new.source_spans) loop
    if pg_catalog.jsonb_typeof(span->'start_char') <> 'number'
       or pg_catalog.jsonb_typeof(span->'end_char') <> 'number'
       or pg_catalog.jsonb_typeof(span->'start_line') <> 'number'
       or pg_catalog.jsonb_typeof(span->'end_line') <> 'number'
       or (span->>'start_char')::integer < previous_end
       or (span->>'end_char')::integer <= (span->>'start_char')::integer
       or (span->>'end_char')::integer > source_size
       or (span->>'start_line')::integer < 1
       or (span->>'end_line')::integer < (span->>'start_line')::integer then
      raise exception 'chunk source span is invalid';
    end if;
    previous_end := (span->>'end_char')::integer;
  end loop;
  return new;
end $$;
create trigger validate_chunk_spans before insert or update on knowledge.chunks
for each row execute function knowledge.validate_chunk_spans();

create table knowledge.artifacts (
  run_id text not null references knowledge.runs,
  artifact_id text not null,
  kind text not null,
  version_inputs jsonb not null,
  model jsonb,
  status text not null check (status in ('pending', 'failed', 'ready')),
  payload jsonb,
  usage jsonb not null,
  attempts integer not null check (attempts > 0),
  error_code text,
  primary key (run_id, artifact_id),
  check ((status = 'ready') = (payload is not null))
);

create function knowledge.guard_artifact_update() returns trigger
language plpgsql set search_path = '' as $$
begin
  if old.status = 'ready' or
     (old.run_id, old.artifact_id, old.kind, old.version_inputs, old.model)
       is distinct from
     (new.run_id, new.artifact_id, new.kind, new.version_inputs, new.model) or
     new.attempts < old.attempts then
    raise exception 'artifact identity or ready payload is immutable';
  end if;
  return new;
end $$;
create trigger guard_artifact before update on knowledge.artifacts
for each row execute function knowledge.guard_artifact_update();

create table knowledge.contexts (
  run_id text not null,
  artifact_id text not null,
  chunk_id text not null,
  payload jsonb not null,
  primary key (run_id, artifact_id),
  foreign key (run_id, artifact_id) references knowledge.artifacts,
  foreign key (run_id, chunk_id) references knowledge.chunks
);
create table knowledge.cards (
  run_id text not null,
  artifact_id text not null,
  card_id text not null,
  chunk_id text not null,
  payload jsonb not null,
  primary key (run_id, artifact_id, card_id),
  foreign key (run_id, artifact_id) references knowledge.artifacts,
  foreign key (run_id, chunk_id) references knowledge.chunks
);
create table knowledge.summaries (
  run_id text not null,
  artifact_id text not null,
  payload jsonb not null,
  primary key (run_id, artifact_id),
  foreign key (run_id, artifact_id) references knowledge.artifacts
);

create table knowledge.embedding_specs (
  spec_id text primary key check (spec_id ~ '^[0-9a-f]{64}$'),
  provider text not null,
  model text not null,
  model_version text not null,
  dimensions integer not null check (dimensions > 0),
  distance_metric text not null check (distance_metric in ('cosine', 'dot', 'euclidean')),
  input_recipe_version text not null,
  context_prompt_version text,
  chunking_version text not null,
  payload jsonb not null
);
create table knowledge.embedding_vectors (
  run_id text not null,
  chunk_id text not null,
  spec_id text not null references knowledge.embedding_specs,
  artifact_id text not null,
  embedding extensions.vector not null,
  primary key (run_id, chunk_id, spec_id),
  foreign key (run_id, chunk_id) references knowledge.chunks,
  foreign key (run_id, artifact_id) references knowledge.artifacts
);

create function knowledge.validate_vector() returns trigger
language plpgsql set search_path = '' as $$
declare expected integer;
begin
  select dimensions into expected from knowledge.embedding_specs where spec_id = new.spec_id;
  if expected is null or extensions.vector_dims(new.embedding) <> expected then
    raise exception 'embedding dimensions disagree with spec';
  end if;
  return new;
end $$;
create trigger validate_vector before insert or update on knowledge.embedding_vectors
for each row execute function knowledge.validate_vector();

create table knowledge.profiles (
  profile_id text primary key,
  variant text not null check (variant in ('fixed_raw', 'structural_raw')),
  chunking_version text not null,
  spec_id text not null references knowledge.embedding_specs,
  text_config text not null check (text_config = 'simple'),
  required_artifact_kinds jsonb not null default '[]'::jsonb
);
create table knowledge.active_runs (
  profile_id text not null references knowledge.profiles,
  document_id text not null references knowledge.documents,
  run_id text not null,
  selection jsonb not null,
  primary key (profile_id, document_id),
  foreign key (run_id, document_id) references knowledge.runs (run_id, document_id)
);
create table knowledge.corpus_state (
  singleton boolean primary key default true check (singleton),
  profile_id text references knowledge.profiles
);
insert into knowledge.corpus_state (singleton) values (true);

create index chunks_fts_simple on knowledge.chunks
  using gin (to_tsvector('simple'::regconfig, raw_text));
create index chunks_run_variant on knowledge.chunks (run_id, variant, chunking_version);
create index vectors_spec_run on knowledge.embedding_vectors (spec_id, run_id);

-- Profile-specific HNSW indexes are added only after a measured model choice.
do $$ declare item record; begin
  for item in select tablename from pg_tables where schemaname = 'knowledge' loop
    execute format('alter table knowledge.%I enable row level security', item.tablename);
    execute format('create policy runtime_access on knowledge.%I for all to knowledge_runtime using (true) with check (true)', item.tablename);
    execute format('revoke all on knowledge.%I from public, anon, authenticated', item.tablename);
  end loop;
end $$;

grant select, insert on all tables in schema knowledge to knowledge_runtime;
grant update (manifest) on knowledge.runs to knowledge_runtime;
grant update (status, payload, usage, attempts, error_code) on knowledge.artifacts to knowledge_runtime;
grant update (run_id, selection) on knowledge.active_runs to knowledge_runtime;
grant update (profile_id) on knowledge.corpus_state to knowledge_runtime;
grant execute on function knowledge.guard_artifact_update() to knowledge_runtime;
grant execute on function knowledge.validate_vector() to knowledge_runtime;
grant execute on function knowledge.validate_chunk_spans() to knowledge_runtime;
revoke all on all functions in schema knowledge from public, anon, authenticated;
