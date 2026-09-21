-- A kept reference was "fetched" if it merely carried a URL. It now records how
-- much of the source was actually reached when it was kept: 'full text',
-- 'abstract only', 'registry record', 'page text' or 'not read'. NULL = kept
-- before this existed; never checked.
ALTER TABLE learn_entries ADD COLUMN IF NOT EXISTS source_basis TEXT;
