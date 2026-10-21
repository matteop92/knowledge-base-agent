-- The live tag list: tags the classifier may apply (approved) and tags that are never applied or suggested
-- again (blocked). The first read seeds it from config/tags.yaml; after that this table is the source of truth.
create table knowledge.tags (
  name text primary key check (name ~ '^[a-z0-9][a-z0-9 &/-]{0,39}$'),
  status text not null check (status in ('approved', 'blocked')),
  rule text,
  position bigint generated always as identity,
  decided_at timestamptz not null default now()
);

alter table knowledge.tags enable row level security;
create policy runtime_access on knowledge.tags for all to knowledge_runtime using (true) with check (true);
revoke all on knowledge.tags from public, anon, authenticated;
grant select, insert, update on knowledge.tags to knowledge_runtime;
