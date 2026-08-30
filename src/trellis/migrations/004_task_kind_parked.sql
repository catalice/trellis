-- Tasks split into kinds: todo (admin you owe) vs seed (curiosity you might
-- feed — never urgent, never counted, lives in Seeds.md). Plus 'parked'
-- status: consciously shelved, visible on demand — unlike dropped (never again).

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'todo'
    CHECK (kind IN ('todo', 'seed'));

ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_status_check
    CHECK (status IN ('open', 'in_progress', 'done', 'dropped', 'archived', 'parked'));
