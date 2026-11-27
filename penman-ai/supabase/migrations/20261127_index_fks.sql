-- Supabase Performance Advisor: "Unindexed foreign keys" on
-- public.suggestion_history. Postgres does not create FK indexes for you, so
-- add the two standard ones. Guarded because the table is dashboard-created
-- on some projects and absent from fresh installs.

DO $$
BEGIN
    IF to_regclass('public.suggestion_history') IS NULL THEN RETURN; END IF;

    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_suggestion_history_user_id ON public.suggestion_history (user_id)';
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_suggestion_history_draft_id ON public.suggestion_history (draft_id)';
END $$;
