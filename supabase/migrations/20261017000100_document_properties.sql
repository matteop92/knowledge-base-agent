-- Generated properties (type, tags, people, date, description, language) per file
-- revision. They only fill what the file's own frontmatter leaves out.
create table knowledge.document_properties (
  document_id text not null references knowledge.documents,
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  status text not null check (status in ('ready', 'failed')),
  properties jsonb not null default '{}'::jsonb,
  proposed_tag text,
  model jsonb,
  prompt_version text not null,
  error text,
  created_at timestamptz not null default now(),
  primary key (document_id, source_sha256)
);

alter table knowledge.document_properties enable row level security;
create policy runtime_access on knowledge.document_properties for all to knowledge_runtime using (true) with check (true);
revoke all on knowledge.document_properties from public, anon, authenticated;
grant select, insert, update on knowledge.document_properties to knowledge_runtime;
