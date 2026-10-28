-- One flat row per active source: type, tags, authors, guests, date and first-added time.
-- Type is the note's own type or source_type, else its folder, else the classifier's guess.
-- Read-only convenience over knowledge.runs, knowledge.document_properties and knowledge.documents;
-- it mirrors how app/agent/properties.py works the values out and changes no stored data.
create or replace view knowledge.source_properties as
select
  d.document_id,
  d.relative_path,
  split_part(d.relative_path, '/', 2)                                   as folder,
  coalesce(r.parsed->'metadata'->>'title', d.relative_path)             as title,
  lower(coalesce(
    nullif(trim(r.parsed->'metadata'->'frontmatter'->>'type'), ''),
    nullif(trim(r.parsed->'metadata'->'frontmatter'->>'source_type'), ''),
    case lower(split_part(d.relative_path, '/', 2))
      when 'articles' then 'article' when 'transcripts' then 'transcript'
      when 'books' then 'book' when 'book highlights' then 'highlights'
      when 'notes' then 'note' end,
    nullif(trim(p.properties->>'source_type'), '')))                    as type,
  case when nullif(trim(r.parsed->'metadata'->'frontmatter'->>'type'), '') is not null
         or nullif(trim(r.parsed->'metadata'->'frontmatter'->>'source_type'), '') is not null then 'your note'
       when lower(split_part(d.relative_path, '/', 2)) in ('articles', 'transcripts', 'books', 'book highlights', 'notes')
         then 'folder'
       else 'ai' end                                                    as type_source,
  coalesce(r.parsed->'metadata'->'tags', '[]'::jsonb)                   as your_tags,
  coalesce(p.properties->'tags', '[]'::jsonb)                           as ai_tags,
  coalesce(r.parsed->'metadata'->'authors', '[]'::jsonb)                as authors,
  coalesce(r.parsed->'metadata'->'guests', '[]'::jsonb)                 as guests,
  left(coalesce(r.parsed->'metadata'->>'published', r.parsed->'metadata'->>'date',
                r.parsed->'metadata'->>'created', p.properties->>'date'), 10) as date,
  (select min(x.loaded_at) from knowledge.runs x where x.document_id = r.document_id) as first_loaded
from knowledge.runs r
join knowledge.active_runs ar on ar.run_id = r.run_id
join knowledge.documents d on d.document_id = r.document_id
left join knowledge.document_properties p
  on p.document_id = r.document_id and p.source_sha256 = r.source_sha256 and p.status = 'ready';

grant select on knowledge.source_properties to knowledge_runtime;
