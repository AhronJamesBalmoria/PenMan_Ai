-- Add stylometric running statistics to author_profiles.
-- Additive only: the existing style_embedding column and pgvector extension are
-- intentionally left in place. RLS policies on author_profiles are unchanged;
-- users can still read/write only their own rows (user_id = auth.uid()).
--
-- feature_mean / feature_m2 are jsonb arrays aligned with
-- app.services.stylometry.FEATURE_NAMES (currently 113 entries).

ALTER TABLE public.author_profiles
    ADD COLUMN IF NOT EXISTS feature_mean jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS feature_m2  jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS essay_count integer NOT NULL DEFAULT 0;

-- Existing rows start with empty stats and essay_count = 0, so drift checks
-- return "not enough history" until 3 new essays are profiled.
