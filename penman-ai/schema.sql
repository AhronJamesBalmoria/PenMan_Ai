-- Penman AI: author_profiles table stores per-user style baselines.
-- The vector column uses pgvector's 384-dimension BGE-small embedding.
-- One row per (user_id, essay_type); the unique constraint prevents
-- a creative-essay profile from overwriting the expository baseline.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS author_profiles (
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
    style_embedding vector(384) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One baseline per user+persona; the upsert in app/services/style_profiler.py
    -- uses on_conflict="user_id,essay_type".
    UNIQUE (user_id, essay_type)
);

-- Helpful index for the drift check: find the user's profile quickly.
CREATE INDEX IF NOT EXISTS idx_author_profiles_user_essay
    ON author_profiles (user_id, essay_type);

-- Optional: GIN-ish distance operator for cosine similarity comparisons
-- (handled in application code via FastEmbed + pgvector).