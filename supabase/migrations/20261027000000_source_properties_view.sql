-- One flat row per active source: type, tags, authors, guests, date and first-added time.
-- Type is the note's own type or source_type, else its folder, else the classifier's guess.
-- Read-only convenience over knowledge.runs, knowledge.document_properties and knowledge.documents;
-- it mirrors how app/agent/properties.py works the values out and changes no stored data.
-- first_loaded is the raw time the file was first loaded. The app replaces it with the note's own
-- created or filename date for notes from the first bulk load (BULK_LOAD_CUTOFF in properties.py).
create or replace view knowledge.source_properties as
select
  d.document_id,
  d.relative_path,
  f.name                                                                as folder,
  coalesce(r.parsed->'metadata'->>'title', d.relative_path)             as title,
  lower(coalesce(
    nullif(trim(r.parsed->'metadata'->'frontmatter'->>'type'), ''),
    nullif(trim(r.parsed->'metadata'->'frontmatter'->>'source_type'), ''),
    case lower(f.name)
      when 'articles' then 'article' when 'transcripts' then 'transcript'
      when 'books' then 'book' when 'book highlights' then 'highlights'
      when 'notes' then 'note' end,
    nullif(trim(p.properties->>'source_type'), '')))                    as type,
  case when nullif(trim(r.parsed->'metadata'->'frontmatter'->>'type'), '') is not null
         or nullif(trim(r.parsed->'metadata'->'frontmatter'->>'source_type'), '') is not null then 'your note'
       when lower(f.name) in ('articles', 'transcripts', 'books', 'book highlights', 'notes')
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
join knowledge.corpus_state s on s.profile_id = ar.profile_id
join knowledge.documents d on d.document_id = r.document_id
-- the note's immediate parent folder, as PurePosixPath(...).parent.name does in source_type()
cross join lateral (select (string_to_array(d.relative_path, '/'))[cardinality(string_to_array(d.relative_path, '/')) - 1] as name) f
left join knowledge.document_properties p
  on p.document_id = r.document_id and p.source_sha256 = r.source_sha256 and p.status = 'ready';

grant select on knowledge.source_properties to knowledge_runtime;
