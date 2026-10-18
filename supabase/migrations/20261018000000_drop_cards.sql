-- Evidence cards were removed from the system in October 2026; the loader ignores
-- historical card artifacts and nothing reads or writes this table any more.
drop table if exists knowledge.cards;
