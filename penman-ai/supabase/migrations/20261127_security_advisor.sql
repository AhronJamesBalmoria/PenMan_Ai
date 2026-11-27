-- Supabase Security Advisor cleanup.
--  * Function Search Path Mutable: pin search_path on the trigger functions.
--  * SECURITY DEFINER exposure: handle_new_user is a trigger function, so the
--    app never calls it directly. Revoke EXECUTE from PUBLIC/anon/authenticated.
--  * Extension in Public: pgvector is unused since style drift moved to pure
--    Python. If a dashboard-created column still depends on it, DROP will fail;
--    remove that dependency first, then re-run this migration.
--
-- Guarded because handle_updated_at / handle_new_user are dashboard-created
-- and do not exist on every project. Idempotent.

-- 1. Our canonical trigger function.
ALTER FUNCTION public.set_updated_at() SET search_path = '';

-- 2. Dashboard-created trigger functions, if present.
DO $$
BEGIN
    IF to_regprocedure('public.handle_updated_at()') IS NOT NULL THEN
        EXECUTE format(
            'ALTER FUNCTION public.handle_updated_at() SET search_path = %L', ''
        );
    END IF;

    IF to_regprocedure('public.handle_new_user()') IS NOT NULL THEN
        EXECUTE format(
            'ALTER FUNCTION public.handle_new_user() SET search_path = %L', ''
        );
        EXECUTE 'REVOKE ALL ON FUNCTION public.handle_new_user() '
                'FROM PUBLIC, anon, authenticated';
    END IF;
END $$;

-- 3. Unused extension.
DROP EXTENSION IF EXISTS vector;
