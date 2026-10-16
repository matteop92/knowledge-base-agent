-- Conversations, sign-in sessions, feedback, capture jobs and model spend for
-- the hosted web app, plus the grant that lets the indexer retire a deleted file.

create table knowledge.conversations (
  conversation_id uuid primary key default gen_random_uuid(),
  title text not null check (length(title) between 1 and 200),
  created_at timestamptz not null default now(),
  last_activity_at timestamptz not null default now()
);
create table knowledge.turns (
  turn_id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references knowledge.conversations on delete cascade,
  position integer not null check (position >= 1),
  question text not null check (length(question) > 0),
  standalone_query text,
  status text not null default 'running'
    check (status in ('running', 'done', 'done_with_gaps', 'failed')),
  receipt jsonb,
  cost_usd numeric(12, 6) not null default 0 check (cost_usd >= 0),
  error text,
  created_at timestamptz not null default now(),
  finished_at timestamptz,
  unique (conversation_id, position),
  check ((status = 'running') = (finished_at is null))
);
create table knowledge.capture_jobs (
  job_id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('screenshot', 'youtube', 'text', 'markdown')),
  status text not null default 'running' check (status in ('running', 'done', 'failed')),
  vault_path text,
  error text,
  cost_usd numeric(12, 6) not null default 0 check (cost_usd >= 0),
  created_at timestamptz not null default now(),
  finished_at timestamptz
);
-- Only a hash of the cookie value is stored, so a leaked row cannot be replayed.
create table knowledge.sessions (
  token_sha256 text primary key check (token_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  last_used_at timestamptz not null default now(),
  expires_at timestamptz not null
);
create table knowledge.feedback (
  turn_id uuid primary key references knowledge.turns on delete cascade,
  verdict text not null check (verdict = 'down'),
  note text,
  created_at timestamptz not null default now()
);
create table knowledge.spend (
  spend_id uuid primary key default gen_random_uuid(),
  day date not null default (now() at time zone 'utc')::date,
  source text not null
    check (source in ('answer', 'rewrite', 'indexing', 'properties', 'vision', 'transcript', 'bulk_load')),
  estimated_usd numeric(12, 6) not null check (estimated_usd >= 0),
  job_id text,
  created_at timestamptz not null default now()
);

create index turns_running on knowledge.turns (conversation_id) where status = 'running';
create index conversations_activity on knowledge.conversations (last_activity_at desc);
create index sessions_expiry on knowledge.sessions (expires_at);
create index spend_day on knowledge.spend (day);

-- Name each new table; the first migration's schema-wide loop would fail on
-- tables that already have the runtime policy.
do $$ declare name text; begin
  foreach name in array array['conversations', 'turns', 'capture_jobs', 'sessions', 'feedback', 'spend'] loop
    execute format('alter table knowledge.%I enable row level security', name);
    execute format('create policy runtime_access on knowledge.%I for all to knowledge_runtime using (true) with check (true)', name);
    execute format('revoke all on knowledge.%I from public, anon, authenticated', name);
    execute format('grant select, insert, update on knowledge.%I to knowledge_runtime', name);
  end loop;
end $$;

grant delete on knowledge.sessions, knowledge.feedback to knowledge_runtime;
-- Retiring a deleted or emptied vault file removes only its active-run row;
-- runs and chunks stay as history.
grant delete on knowledge.active_runs to knowledge_runtime;
