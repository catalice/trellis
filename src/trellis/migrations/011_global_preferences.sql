-- Preferences gained a 'global' domain (loaded every turn). 'learn' was removed
-- from the tool's options: the learn house isn't registered, so anything saved
-- there could never load — re-home those preferences as global. Idempotent: only
-- moves rows when the user has no global row yet (none exist in practice).
UPDATE user_preferences p
   SET domain = 'global', updated_at = NOW()
 WHERE p.domain = 'learn'
   AND NOT EXISTS (
        SELECT 1 FROM user_preferences g
         WHERE g.user_id = p.user_id AND g.domain = 'global'
   );
