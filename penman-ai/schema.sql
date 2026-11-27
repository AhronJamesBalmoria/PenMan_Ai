-- Penman AI: canonical schema for a fresh Supabase project.
--
-- Existing projects must use the additive migrations in supabase/migrations/
-- instead; those drop the old embedding column and reset stylometric stats.
-- Row level security is part of the schema: the app always talks to PostgREST
-- with the signed-in user's JWT, so RLS is the actual authorisation boundary.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- profiles: consent state, one row per auth user.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
    display_name TEXT NOT NULL DEFAULT '',
    consent_given BOOLEAN NOT NULL DEFAULT false,
    consent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- drafts: plain text plus Quill HTML.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    essay_type TEXT NOT NULL DEFAULT 'expository' CHECK (essay_type IN (
        'argumentative', 'expository', 'narrative', 'descriptive',
        'persuasive', 'research', 'creative'
    )),
    content TEXT NOT NULL DEFAULT '',
    plain_text TEXT NOT NULL DEFAULT '',
    word_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_drafts_user_updated
    ON public.drafts (user_id, updated_at DESC);

-- ---------------------------------------------------------------------------
-- author_profiles: one stylometric baseline per (user, essay type).
-- feature_mean / feature_m2 are jsonb arrays aligned with
-- app.services.stylometry.FEATURE_NAMES. There is no embedding column: drift
-- is computed in pure Python from function words, punctuation and rhythm.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.author_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    essay_type TEXT NOT NULL CHECK (essay_type IN (
        'argumentative', 'expository', 'narrative', 'descriptive',
        'persuasive', 'research', 'creative'
    )),
    tone TEXT NOT NULL DEFAULT 'neutral',
    register TEXT NOT NULL DEFAULT 'standard',
    avg_sentence_length NUMERIC NOT NULL DEFAULT 15,
    vocabulary_richness NUMERIC NOT NULL DEFAULT 0.5,
    feature_mean JSONB NOT NULL DEFAULT '[]'::jsonb,
    feature_m2 JSONB NOT NULL DEFAULT '[]'::jsonb,
    essay_count INTEGER NOT NULL DEFAULT 0,
    last_content_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- The upsert in app/services/style_profiler.py uses
    -- on_conflict="user_id,essay_type".
    UNIQUE (user_id, essay_type)
);

CREATE INDEX IF NOT EXISTS idx_author_profiles_user_essay
    ON public.author_profiles (user_id, essay_type);

-- ---------------------------------------------------------------------------
-- updated_at: the app writes it explicitly, this trigger is the safety net
-- for any other writer (SQL, dashboard, future code paths).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS profiles_set_updated_at ON public.profiles;
CREATE TRIGGER profiles_set_updated_at
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS drafts_set_updated_at ON public.drafts;
CREATE TRIGGER drafts_set_updated_at
    BEFORE UPDATE ON public.drafts
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS author_profiles_set_updated_at ON public.author_profiles;
CREATE TRIGGER author_profiles_set_updated_at
    BEFORE UPDATE ON public.author_profiles
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ---------------------------------------------------------------------------
-- RLS: a signed-in user may only touch their own rows. The anon key alone
-- must never be able to read drafts, consent state or style baselines.
-- Exactly one policy per action avoids "Multiple Permissive Policies"
-- warnings; wrapping the uid lookup in a scalar subquery lets Postgres cache
-- it for the whole statement instead of re-running it per row.
-- ---------------------------------------------------------------------------
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.author_profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "own profile" ON public.profiles;
DROP POLICY IF EXISTS "own drafts" ON public.drafts;
DROP POLICY IF EXISTS "own style profiles" ON public.author_profiles;

CREATE POLICY "Users can view own profile" ON public.profiles
    FOR SELECT TO authenticated USING ((select auth.uid()) = id);
CREATE POLICY "Users can insert own profile" ON public.profiles
    FOR INSERT TO authenticated WITH CHECK ((select auth.uid()) = id);
CREATE POLICY "Users can update own profile" ON public.profiles
    FOR UPDATE TO authenticated
    USING ((select auth.uid()) = id) WITH CHECK ((select auth.uid()) = id);
CREATE POLICY "Users can delete own profile" ON public.profiles
    FOR DELETE TO authenticated USING ((select auth.uid()) = id);

CREATE POLICY "Users can view own drafts" ON public.drafts
    FOR SELECT TO authenticated USING ((select auth.uid()) = user_id);
CREATE POLICY "Users can insert own drafts" ON public.drafts
    FOR INSERT TO authenticated WITH CHECK ((select auth.uid()) = user_id);
CREATE POLICY "Users can update own drafts" ON public.drafts
    FOR UPDATE TO authenticated
    USING ((select auth.uid()) = user_id) WITH CHECK ((select auth.uid()) = user_id);
CREATE POLICY "Users can delete own drafts" ON public.drafts
    FOR DELETE TO authenticated USING ((select auth.uid()) = user_id);

CREATE POLICY "Users can view own style profile" ON public.author_profiles
    FOR SELECT TO authenticated USING ((select auth.uid()) = user_id);
CREATE POLICY "Users can insert own style profile" ON public.author_profiles
    FOR INSERT TO authenticated WITH CHECK ((select auth.uid()) = user_id);
CREATE POLICY "Users can update own style profile" ON public.author_profiles
    FOR UPDATE TO authenticated
    USING ((select auth.uid()) = user_id) WITH CHECK ((select auth.uid()) = user_id);
CREATE POLICY "Users can delete own style profile" ON public.author_profiles
    FOR DELETE TO authenticated USING ((select auth.uid()) = user_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.profiles TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.drafts TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.author_profiles TO authenticated;

-- suggestion_history is dashboard-created on some projects. Apply the same
-- four optimized policies when it exists, without breaking a fresh install.
DO $$
BEGIN
    IF to_regclass('public.suggestion_history') IS NULL THEN RETURN; END IF;
    EXECUTE 'ALTER TABLE public.suggestion_history ENABLE ROW LEVEL SECURITY';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.suggestion_history TO authenticated';
    EXECUTE 'CREATE POLICY "Users can view own suggestion history" ON public.suggestion_history FOR SELECT TO authenticated USING ((select auth.uid()) = user_id)';
    EXECUTE 'CREATE POLICY "Users can insert own suggestion history" ON public.suggestion_history FOR INSERT TO authenticated WITH CHECK ((select auth.uid()) = user_id)';
    EXECUTE 'CREATE POLICY "Users can update own suggestion history" ON public.suggestion_history FOR UPDATE TO authenticated USING ((select auth.uid()) = user_id) WITH CHECK ((select auth.uid()) = user_id)';
    EXECUTE 'CREATE POLICY "Users can delete own suggestion history" ON public.suggestion_history FOR DELETE TO authenticated USING ((select auth.uid()) = user_id)';

    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_suggestion_history_user_id ON public.suggestion_history (user_id)';
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_suggestion_history_draft_id ON public.suggestion_history (draft_id)';
END $$;
