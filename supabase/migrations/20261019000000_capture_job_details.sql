-- Capture jobs show the note title while saving and whether the new note was indexed at once.
alter table knowledge.capture_jobs
  add column title text,
  add column indexed boolean not null default false;
