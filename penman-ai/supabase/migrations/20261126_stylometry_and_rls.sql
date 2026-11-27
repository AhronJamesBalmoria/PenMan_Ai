-- Bring an existing project in line with schema.sql.
-- Additive/idempotent: safe to re-run.

-- 1. The BGE embedding was replaced by pure-Python stylometry. The column is
--    dead weight and its NOT NULL constraint blocks the new upsert entirely.
ALTER TABLE public.author_profiles DROP COLUMN IF EXISTS style_embedding;

-- 2. The paragraph-length feature now splits on single newlines (Quill blocks)
--    instead of blank lines, so old vectors measure something different. Reset
--    the running stats: drift checks report "not enough history" until three
--    fresh essays have been profiled.
UPDATE public.author_profiles
   SET feature_mean = '[]'::jsonb,
       feature_m2 = '[]'::jsonb,
       essay_count = 0
 WHERE essay_count <> 0 OR feature_mean <> '[]'::jsonb OR feature_m2 <> '[]'::jsonb;

-- 3. Every table with an updated_at trigger must actually HAVE that column.
--    Older projects created profiles/drafts in the dashboard without it, which
--    made BEFORE UPDATE fail with 42703: record "new" has no field "updated_at".
--    Re-running this migration repairs those databases in place.
DO $$
BEGIN
    EXECUTE 'ALTER TABLE public.author_profiles ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()';

    IF to_regclass('public.drafts') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.drafts ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()';
    END IF;

    IF to_regclass('public.profiles') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()';
    END IF;
END $$;

-- 4. updated_at trigger: the app writes it explicitly, but a trigger keeps it
--    honest for every other writer. A column DEFAULT only fires on INSERT.
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

ALTER TABLE public.author_profiles ENABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS author_profiles_set_updated_at ON public.author_profiles;
CREATE TRIGGER author_profiles_set_updated_at
    BEFORE UPDATE ON public.author_profiles
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP POLICY IF EXISTS "own style profiles" ON public.author_profiles;
CREATE POLICY "own style profiles" ON public.author_profiles
    FOR ALL USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.author_profiles TO authenticated;

-- 5. Same protection for drafts and profiles. Guarded so the migration works
--    whether those tables were created in the dashboard or by schema.sql.
DO $$
BEGIN
    IF to_regclass('public.drafts') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.drafts ENABLE ROW LEVEL SECURITY';
        EXECUTE 'DROP TRIGGER IF EXISTS drafts_set_updated_at ON public.drafts';
        EXECUTE 'CREATE TRIGGER drafts_set_updated_at BEFORE UPDATE ON public.drafts '
                'FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()';
        EXECUTE 'DROP POLICY IF EXISTS "own drafts" ON public.drafts';
        EXECUTE 'CREATE POLICY "own drafts" ON public.drafts FOR ALL '
                'USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id)';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.drafts TO authenticated';
    END IF;

    IF to_regclass('public.profiles') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY';
        EXECUTE 'DROP TRIGGER IF EXISTS profiles_set_updated_at ON public.profiles';
        EXECUTE 'CREATE TRIGGER profiles_set_updated_at BEFORE UPDATE ON public.profiles '
                'FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()';
        EXECUTE 'DROP POLICY IF EXISTS "own profile" ON public.profiles';
        EXECUTE 'CREATE POLICY "own profile" ON public.profiles FOR ALL '
                'USING (auth.uid() = id) WITH CHECK (auth.uid() = id)';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.profiles TO authenticated';
    END IF;
END $$;
