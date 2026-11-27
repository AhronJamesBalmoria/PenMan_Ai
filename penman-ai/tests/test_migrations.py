"""Guardrails for the Supabase migration that repairs existing projects."""
from app.config import PROJECT_ROOT

MIGRATION = (
    PROJECT_ROOT
    / "supabase"
    / "migrations"
    / "20261126_stylometry_and_rls.sql"
)
RLS_MIGRATION = (
    PROJECT_ROOT
    / "supabase"
    / "migrations"
    / "20261127_optimize_rls.sql"
)
INDEX_MIGRATION = (
    PROJECT_ROOT
    / "supabase"
    / "migrations"
    / "20261127_index_fks.sql"
)
SECURITY_MIGRATION = (
    PROJECT_ROOT
    / "supabase"
    / "migrations"
    / "20261127_security_advisor.sql"
)
SCHEMA = PROJECT_ROOT / "schema.sql"

TABLES = ("profiles", "drafts", "author_profiles", "suggestion_history")
ACTIONS = ("SELECT", "INSERT", "UPDATE", "DELETE")


def test_updated_at_columns_are_created_before_triggers():
    """A trigger on a missing column fails every UPDATE with SQLSTATE 42703."""
    sql = MIGRATION.read_text(encoding="utf-8")

    first_add = sql.index("ADD COLUMN IF NOT EXISTS updated_at")
    first_trigger = sql.index("CREATE TRIGGER author_profiles_set_updated_at")
    assert first_add < first_trigger

    for table in ("author_profiles", "drafts", "profiles"):
        assert (
            f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS updated_at"
            in sql
        )


def _assert_optimized_policies(sql: str) -> None:
    """Four single-action policies per table, all using the cached uid."""
    # Ignore comments and collapse line breaks so multi-line CREATE POLICY
    # statements can be matched as single strings.
    sql = " ".join(
        line.strip()
        for line in sql.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    )
    assert sql.upper().count("CREATE POLICY") == len(TABLES) * len(ACTIONS)
    for table in TABLES:
        assert f"ON public.{table} " in sql
        for action in ACTIONS:
            assert f"ON public.{table} FOR {action} TO authenticated" in sql
        assert f"public.{table} ENABLE ROW LEVEL SECURITY" in sql

    assert "(select auth.uid())" in sql
    assert "USING (auth.uid()" not in sql
    assert "WITH CHECK (auth.uid()" not in sql
    assert "FOR ALL" not in sql.upper()


def test_rls_migration_drops_every_existing_policy():
    sql = RLS_MIGRATION.read_text(encoding="utf-8")

    assert "FROM pg_policies" in sql
    assert "DROP POLICY IF EXISTS %I ON public.%I" in sql
    _assert_optimized_policies(sql)


def test_canonical_schema_has_the_same_optimized_policies():
    sql = SCHEMA.read_text(encoding="utf-8")

    _assert_optimized_policies(sql)
    # The dashboard-created table must remain optional on fresh installs.
    assert "to_regclass('public.suggestion_history')" in sql


def _assert_suggestion_history_indexes(sql: str) -> None:
    sql = " ".join(
        line.strip()
        for line in sql.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    )
    assert "to_regclass('public.suggestion_history')" in sql
    assert (
        "CREATE INDEX IF NOT EXISTS idx_suggestion_history_user_id "
        "ON public.suggestion_history (user_id)"
    ) in sql
    assert (
        "CREATE INDEX IF NOT EXISTS idx_suggestion_history_draft_id "
        "ON public.suggestion_history (draft_id)"
    ) in sql


def test_index_migration_covers_both_foreign_keys():
    _assert_suggestion_history_indexes(INDEX_MIGRATION.read_text(encoding="utf-8"))


def test_canonical_schema_has_the_same_indexes():
    _assert_suggestion_history_indexes(SCHEMA.read_text(encoding="utf-8"))


def test_security_migration_hardens_functions_and_drops_vector():
    sql = SECURITY_MIGRATION.read_text(encoding="utf-8")

    assert "ALTER FUNCTION public.set_updated_at() SET search_path = ''" in sql
    assert "ALTER FUNCTION public.handle_updated_at() SET search_path = %L" in sql
    assert "ALTER FUNCTION public.handle_new_user() SET search_path = %L" in sql
    assert "REVOKE ALL ON FUNCTION public.handle_new_user()" in sql
    assert "DROP EXTENSION IF EXISTS vector" in sql


def test_canonical_function_pins_search_path():
    sql = SCHEMA.read_text(encoding="utf-8")
    block = sql.split("CREATE OR REPLACE FUNCTION public.set_updated_at()", 1)[1]
    block = block.split("AS $$", 1)[0]
    assert "SET search_path = ''" in block
