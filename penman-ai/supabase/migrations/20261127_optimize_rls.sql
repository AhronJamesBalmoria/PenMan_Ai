-- Replace every RLS policy on the app's tables with exactly four narrow
-- policies per table (SELECT / INSERT / UPDATE / DELETE). This removes
-- "Multiple Permissive Policies" warnings (no overlapping FOR ALL rules) and
-- "Auth RLS Initialization Plan" warnings ((select auth.uid()) is evaluated
-- once per query instead of once per row).
--
-- Apply this AFTER 20261126_stylometry_and_rls.sql. Re-running an older
-- migration afterwards would reinstate its FOR ALL policies.
--
-- Idempotent: all existing policies are dropped before the new ones are made.

-- 1. Drop EVERY existing policy on the four tables, including policies whose
--    names we do not know (dashboard-created ones, old FOR ALL rules, etc.).
DO $$
DECLARE
    pol record;
BEGIN
    FOR pol IN
        SELECT policyname, tablename
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename IN ('profiles', 'drafts', 'author_profiles', 'suggestion_history')
    LOOP
        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON public.%I', pol.policyname, pol.tablename
        );
    END LOOP;
END $$;

-- 2. profiles (owner column: id)
DO $$
BEGIN
    IF to_regclass('public.profiles') IS NULL THEN RETURN; END IF;
    EXECUTE 'ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.profiles TO authenticated';
    EXECUTE 'CREATE POLICY "Users can view own profile" ON public.profiles FOR SELECT TO authenticated USING ((select auth.uid()) = id)';
    EXECUTE 'CREATE POLICY "Users can insert own profile" ON public.profiles FOR INSERT TO authenticated WITH CHECK ((select auth.uid()) = id)';
    EXECUTE 'CREATE POLICY "Users can update own profile" ON public.profiles FOR UPDATE TO authenticated USING ((select auth.uid()) = id) WITH CHECK ((select auth.uid()) = id)';
    EXECUTE 'CREATE POLICY "Users can delete own profile" ON public.profiles FOR DELETE TO authenticated USING ((select auth.uid()) = id)';
END $$;

-- 3. drafts (owner column: user_id)
DO $$
BEGIN
    IF to_regclass('public.drafts') IS NULL THEN RETURN; END IF;
    EXECUTE 'ALTER TABLE public.drafts ENABLE ROW LEVEL SECURITY';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.drafts TO authenticated';
    EXECUTE 'CREATE POLICY "Users can view own drafts" ON public.drafts FOR SELECT TO authenticated USING ((select auth.uid()) = user_id)';
    EXECUTE 'CREATE POLICY "Users can insert own drafts" ON public.drafts FOR INSERT TO authenticated WITH CHECK ((select auth.uid()) = user_id)';
    EXECUTE 'CREATE POLICY "Users can update own drafts" ON public.drafts FOR UPDATE TO authenticated USING ((select auth.uid()) = user_id) WITH CHECK ((select auth.uid()) = user_id)';
    EXECUTE 'CREATE POLICY "Users can delete own drafts" ON public.drafts FOR DELETE TO authenticated USING ((select auth.uid()) = user_id)';
END $$;

-- 4. author_profiles (owner column: user_id)
DO $$
BEGIN
    IF to_regclass('public.author_profiles') IS NULL THEN RETURN; END IF;
    EXECUTE 'ALTER TABLE public.author_profiles ENABLE ROW LEVEL SECURITY';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.author_profiles TO authenticated';
    EXECUTE 'CREATE POLICY "Users can view own style profile" ON public.author_profiles FOR SELECT TO authenticated USING ((select auth.uid()) = user_id)';
    EXECUTE 'CREATE POLICY "Users can insert own style profile" ON public.author_profiles FOR INSERT TO authenticated WITH CHECK ((select auth.uid()) = user_id)';
    EXECUTE 'CREATE POLICY "Users can update own style profile" ON public.author_profiles FOR UPDATE TO authenticated USING ((select auth.uid()) = user_id) WITH CHECK ((select auth.uid()) = user_id)';
    EXECUTE 'CREATE POLICY "Users can delete own style profile" ON public.author_profiles FOR DELETE TO authenticated USING ((select auth.uid()) = user_id)';
END $$;

-- 5. suggestion_history (owner column: user_id). The table is created in the
--    dashboard on some projects, so guard it rather than failing a fresh DB.
DO $$
BEGIN
    IF to_regclass('public.suggestion_history') IS NULL THEN RETURN; END IF;
    EXECUTE 'ALTER TABLE public.suggestion_history ENABLE ROW LEVEL SECURITY';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.suggestion_history TO authenticated';
    EXECUTE 'CREATE POLICY "Users can view own suggestion history" ON public.suggestion_history FOR SELECT TO authenticated USING ((select auth.uid()) = user_id)';
    EXECUTE 'CREATE POLICY "Users can insert own suggestion history" ON public.suggestion_history FOR INSERT TO authenticated WITH CHECK ((select auth.uid()) = user_id)';
    EXECUTE 'CREATE POLICY "Users can update own suggestion history" ON public.suggestion_history FOR UPDATE TO authenticated USING ((select auth.uid()) = user_id) WITH CHECK ((select auth.uid()) = user_id)';
    EXECUTE 'CREATE POLICY "Users can delete own suggestion history" ON public.suggestion_history FOR DELETE TO authenticated USING ((select auth.uid()) = user_id)';
END $$;
