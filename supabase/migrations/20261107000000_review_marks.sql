-- Review marks: the owner's Done and Again marks on single passages, and how often Again passages come back.
-- A mark belongs to a passage key (the same key `knowledge.feed_passages` derives), so a passage whose words change
-- in the vault gets a new key and loses its mark. last_served_at is when the interleave last handed an Again
-- passage out; it orders the Again passages so they take turns.

create table knowledge.feed_marks (
  passage_key text primary key check (passage_key ~ '^[0-9a-f]{32}$'),
  document_id text not null references knowledge.documents,
  mark text not null check (mark in ('again', 'done')),
  marked_at timestamptz not null default now(),
  last_served_at timestamptz
);
create index feed_marks_by_mark on knowledge.feed_marks (mark, marked_at desc);

-- One row: one Again passage in `again_every` served is an Again passage (10 Normal, 5 Sometimes, 3 Often, 2 Frequently).
create table knowledge.feed_settings (
  singleton boolean primary key default true check (singleton),
  again_every integer not null default 10 check (again_every in (10, 5, 3, 2))
);
insert into knowledge.feed_settings (singleton) values (true);

alter table knowledge.feed_marks enable row level security;
alter table knowledge.feed_settings enable row level security;
create policy runtime_access on knowledge.feed_marks for all to knowledge_runtime using (true) with check (true);
create policy runtime_access on knowledge.feed_settings for all to knowledge_runtime using (true) with check (true);
revoke all on knowledge.feed_marks, knowledge.feed_settings from public, anon, authenticated;
grant select, insert, update, delete on knowledge.feed_marks to knowledge_runtime;
grant select, update on knowledge.feed_settings to knowledge_runtime;
