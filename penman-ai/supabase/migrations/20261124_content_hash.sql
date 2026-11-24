-- Add a content hash so repeated analyses of the same essay do not inflate the
-- running stylometric baseline. Additive only; RLS unchanged.
ALTER TABLE public.author_profiles
    ADD COLUMN IF NOT EXISTS last_content_hash text;
