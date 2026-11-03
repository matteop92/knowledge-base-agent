-- Review: one passage at a time from the indexed vault.
-- Passages are derived from the active runs; the only stored data is the picks (Matt's own selections, with
-- the selected words and a short prefix and suffix for re-anchoring) and the log of what was shown.

-- A selection Matt added to Review from the Sources reader. Offsets are code points into the run's source_text
-- at source_sha256; an edit to the file re-anchors the pick by its exact words or removes it (removed_at).
create table knowledge.feed_picks (
  pick_id uuid primary key default gen_random_uuid(),
  document_id text not null references knowledge.documents,
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  start_char integer not null check (start_char >= 0),
  end_char integer not null,
  exact text not null check (length(exact) > 0),
  prefix text not null default '',
  suffix text not null default '',
  created_at timestamptz not null default now(),
  removed_at timestamptz,
  check (end_char > start_char)
);
create index feed_picks_live on knowledge.feed_picks (document_id) where removed_at is null;

-- What was on screen long enough to count as shown, per shuffle round. The round is the highest one in the log.
create table knowledge.feed_shown (
  seq bigint generated always as identity primary key,
  shown_at timestamptz not null default now(),
  round integer not null check (round >= 1),
  passage_key text not null check (passage_key ~ '^[0-9a-f]{32}$'),
  document_id text not null references knowledge.documents,
  unique (round, passage_key)
);

alter table knowledge.feed_picks enable row level security;
alter table knowledge.feed_shown enable row level security;
create policy runtime_access on knowledge.feed_picks for all to knowledge_runtime using (true) with check (true);
create policy runtime_access on knowledge.feed_shown for all to knowledge_runtime using (true) with check (true);
revoke all on knowledge.feed_picks, knowledge.feed_shown from public, anon, authenticated;
grant select, insert on knowledge.feed_picks, knowledge.feed_shown to knowledge_runtime;
grant update (source_sha256, start_char, end_char, prefix, suffix, removed_at) on knowledge.feed_picks to knowledge_runtime;

-- The pool: every passage Review can show, read from the active runs' source text (nothing is copied).
--   highlight  each book highlight (type highlights); a "My note" in its group travels as `note`
--   quote      each whole quote source, whatever its length
--   post/note  each whole social post or note of at most max_words words with at most max_headings headings
--   pick       each live pick whose file is unchanged since it was made
-- A whole source runs from its first to its last block that is not a heading, separator or image; a leading
-- title heading is left out of the passage but counts as a heading. `text` and `note` are raw source slices
-- (the API cleans them like the reader does). passage_key = md5 of the document id and the raw slice, so the same
-- words in two files stay apart and a pick that matches a highlight collapses into it.
create view knowledge.feed_passages as
with limits as (
  select 400 as max_words, 1 as max_headings
),
src as (
  select r.document_id, r.source_sha256, r.source_text, r.parsed,
         coalesce(p.type, 'note') as type, p.title,
         coalesce((select string_agg(btrim(regexp_replace(a, '^\[\[|\]\]$', '', 'g')), ', ')
                     from jsonb_array_elements_text(p.authors) a
                    where btrim(regexp_replace(a, '^\[\[|\]\]$', '', 'g')) <> ''), '') as author
  from knowledge.runs r
  join knowledge.active_runs ar on ar.run_id = r.run_id
  join knowledge.corpus_state s on s.profile_id = ar.profile_id
  join knowledge.source_properties p on p.document_id = r.document_id
),
blocks as (
  select src.document_id, src.type, (b.value->>'index')::integer as idx, b.value as block
  from src cross join lateral jsonb_array_elements(coalesce(src.parsed->'blocks', '[]'::jsonb)) b
  where src.type not in ('article', 'transcript', 'book')
),
highlights as (
  select h.document_id,
         (h.block->'content_span'->>'start_char')::integer as start_char,
         (h.block->'content_span'->>'end_char')::integer as end_char,
         case when h.block->>'page_reference' is not null then 'p. ' || (h.block->>'page_reference') end as locator,
         (select (n.block->'content_span'->>'start_char')::integer || ':' || (n.block->'content_span'->>'end_char')::integer
            from blocks n
           where n.document_id = h.document_id and n.block->>'content_role' = 'personal_note'
             and n.block->>'group_id' = h.block->>'group_id' and jsonb_typeof(n.block->'content_span') = 'object'
           order by n.idx limit 1) as note_span
  from blocks h
  where h.type = 'highlights' and h.block->>'kind' = 'highlight' and h.block->>'content_role' = 'source'
    and jsonb_typeof(h.block->'content_span') = 'object'
),
wholes as (
  select b.document_id,
         min((b.block->'span'->>'start_char')::integer) filter (where b.block->>'kind' not in ('heading', 'separator', 'asset_reference')) as start_char,
         max((b.block->'span'->>'end_char')::integer) filter (where b.block->>'kind' not in ('heading', 'separator', 'asset_reference')) as end_char,
         count(*) filter (where b.block->>'kind' = 'heading') as headings
  from blocks b
  where b.type <> 'highlights'
  group by b.document_id
),
parts as (
  select h.document_id, 'highlight'::text as kind, 'auto'::text as origin, h.start_char, h.end_char,
         h.locator, h.note_span
  from highlights h
  union all
  select w.document_id, case when s.type = 'quote' then 'quote' when s.type = 'social_post' then 'post' else 'note' end,
         'auto', w.start_char, w.end_char, null, null
  from wholes w
  join src s on s.document_id = w.document_id
  cross join limits l
  where w.start_char is not null
    and (s.type = 'quote'
         or (w.headings <= l.max_headings
             and regexp_count(substr(s.source_text, w.start_char + 1, w.end_char - w.start_char), '\S+') <= l.max_words))
  union all
  select k.document_id, 'pick', 'picked', k.start_char, k.end_char, null, null
  from knowledge.feed_picks k
  join src s on s.document_id = k.document_id and s.source_sha256 = k.source_sha256
  where k.removed_at is null and k.end_char <= char_length(s.source_text)
),
passages as (
  select md5(s.document_id || E'\n' || substr(s.source_text, x.start_char + 1, x.end_char - x.start_char)) as passage_key,
         x.origin, x.kind, x.document_id, x.start_char, x.end_char,
         substr(s.source_text, x.start_char + 1, x.end_char - x.start_char) as text,
         case when x.note_span is not null
              then substr(s.source_text, split_part(x.note_span, ':', 1)::integer + 1,
                          split_part(x.note_span, ':', 2)::integer - split_part(x.note_span, ':', 1)::integer) end as note,
         s.type, s.title, s.author, x.locator
  from parts x
  join src s on s.document_id = x.document_id
  where x.end_char > x.start_char
)
select distinct on (passage_key) passage_key, origin, kind, document_id, start_char, end_char, text, note, type, title, author, locator
from passages
order by passage_key, (origin = 'auto') desc;

revoke all on knowledge.feed_passages from public, anon, authenticated;
grant select on knowledge.feed_passages to knowledge_runtime;
