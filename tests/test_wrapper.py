"""Tests for wrapper.py — verifies idempotency constructs appear in rendered output."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sql_detector import detect
from wrapper import wrap


def _wrap(sql: str, dialect: str = "postgres") -> str:
    return wrap(detect(sql, dialect))


# ---------------------------------------------------------------------------
# PostgreSQL wrappers
# ---------------------------------------------------------------------------

class TestPostgresWrappers:
    def test_create_table_gets_do_block(self):
        out = _wrap("CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);")
        assert "DO $$" in out
        assert "pg_tables" in out
        assert "IF NOT EXISTS" in out

    def test_add_column_checks_information_schema(self):
        out = _wrap("ALTER TABLE users ADD COLUMN phone VARCHAR(20);")
        assert "DO $$" in out
        assert "information_schema.columns" in out
        assert "phone" in out

    def test_alter_column_type_checks_column_exists(self):
        out = _wrap("ALTER TABLE users ALTER COLUMN email TYPE TEXT;")
        assert "DO $$" in out
        assert "information_schema.columns" in out

    def test_create_index_checks_pg_indexes(self):
        out = _wrap("CREATE INDEX idx_users_email ON users(email);")
        assert "DO $$" in out
        assert "pg_indexes" in out
        assert "idx_users_email" in out

    def test_create_unique_index_uses_same_template(self):
        out = _wrap("CREATE UNIQUE INDEX idx_users_email_unique ON users(email);")
        assert "DO $$" in out
        assert "pg_indexes" in out

    def test_add_fk_checks_table_constraints(self):
        out = _wrap(
            "ALTER TABLE orders ADD CONSTRAINT fk_orders_user_id FOREIGN KEY (user_id) REFERENCES users(id);"
        )
        assert "DO $$" in out
        assert "table_constraints" in out
        assert "FOREIGN KEY" in out

    def test_add_unique_constraint(self):
        out = _wrap("ALTER TABLE users ADD CONSTRAINT uq_users_email UNIQUE (email);")
        assert "DO $$" in out
        assert "UNIQUE" in out

    def test_drop_column_checks_existence(self):
        out = _wrap("ALTER TABLE users DROP COLUMN obsolete_field;")
        assert "DO $$" in out
        assert "information_schema.columns" in out

    def test_drop_table_uses_if_exists(self):
        out = _wrap("DROP TABLE obsolete_table CASCADE;")
        assert "IF EXISTS" in out
        assert "DO $$" not in out  # simple one-liner, no block needed

    def test_create_type_checks_pg_type(self):
        out = _wrap("CREATE TYPE user_status AS ENUM ('active', 'inactive');")
        assert "pg_type" in out
        assert "DO $$" in out

    def test_add_not_null_checks_is_nullable(self):
        out = _wrap("ALTER TABLE users ALTER COLUMN email SET NOT NULL;")
        assert "is_nullable" in out.lower() or "IS_NULLABLE" in out

    def test_rename_column_checks_both_sides(self):
        out = _wrap("ALTER TABLE users RENAME COLUMN old_name TO new_name;")
        assert "old_name" in out
        assert "new_name" in out
        assert "DO $$" in out

    def test_drop_constraint_checks_existence(self):
        out = _wrap("ALTER TABLE users DROP CONSTRAINT old_constraint;")
        assert "DO $$" in out
        assert "table_constraints" in out

    def test_create_view_gets_create_or_replace(self):
        out = _wrap("CREATE VIEW v_active_users AS SELECT id FROM users;")
        assert "CREATE OR REPLACE VIEW" in out

    def test_already_idempotent_returned_unchanged(self):
        sql = "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename='t') THEN CREATE TABLE t (id INT); END IF; END $$;"
        out = _wrap(sql)
        assert out == sql.strip()


# ---------------------------------------------------------------------------
# MySQL wrappers
# ---------------------------------------------------------------------------

class TestMysqlWrappers:
    def test_create_table_uses_if_not_exists(self):
        sql = "CREATE TABLE users (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255));"
        out = _wrap(sql, "mysql")
        assert "IF NOT EXISTS" in out

    def test_add_column_uses_prepare_pattern(self):
        out = _wrap("ALTER TABLE users ADD COLUMN phone VARCHAR(20);", "mysql")
        assert "PREPARE stmt" in out
        assert "information_schema.columns" in out
        assert "phone" in out

    def test_create_index_uses_prepare_pattern(self):
        out = _wrap("CREATE INDEX idx_users_email ON users(email);", "mysql")
        assert "PREPARE stmt" in out
        assert "information_schema.statistics" in out

    def test_drop_table_uses_if_exists(self):
        out = _wrap("DROP TABLE obsolete_table;", "mysql")
        assert "IF EXISTS" in out
        assert "PREPARE" not in out

    def test_add_fk_uses_prepare_pattern(self):
        out = _wrap(
            "ALTER TABLE orders ADD CONSTRAINT fk_orders_user_id FOREIGN KEY (user_id) REFERENCES users(id);",
            "mysql",
        )
        assert "PREPARE stmt" in out
        assert "table_constraints" in out

    def test_add_not_null_updates_nulls_first(self):
        out = _wrap(
            "ALTER TABLE users MODIFY COLUMN email VARCHAR(255) NOT NULL;",
            "mysql",
        )
        assert "IS NULL" in out or "is_nullable" in out.lower() or "IS_NULLABLE" in out


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_unknown_ddl_returns_warning_comment(self):
        from sql_detector import DetectionResult, DdlType
        result = DetectionResult(
            ddl_type=DdlType.UNKNOWN,
            already_idempotent=False,
            raw="SELECT 1;",
        )
        out = wrap(result)
        assert "WARNING" in out
        assert "SELECT 1;" in out


# ---------------------------------------------------------------------------
# Header rendering
# ---------------------------------------------------------------------------

class TestHeaderRendering:
    def test_header_contains_guardian_url(self):
        out = _wrap("ALTER TABLE users ADD COLUMN phone VARCHAR(20);")
        assert "https://github.com/postman-eng/flyway-idempotent-guardian" in out

    def test_header_contains_operation_name(self):
        out = _wrap("ALTER TABLE users ADD COLUMN phone VARCHAR(20);")
        assert "-- Operation   : ADD COLUMN" in out

    def test_header_contains_description(self):
        out = _wrap("ALTER TABLE users ADD COLUMN phone VARCHAR(20);")
        assert "-- Description :" in out

    def test_header_contains_pr_author_and_url(self):
        out = wrap(
            detect("ALTER TABLE users ADD COLUMN phone VARCHAR(20);"),
            pr_author="lance",
            pr_url="https://github.com/postman-eng/myrepo/pull/1",
        )
        assert "-- PR Author   : @lance" in out
        assert "https://github.com/postman-eng/myrepo/pull/1" in out

    def test_header_default_author_is_not_empty(self):
        out = _wrap("ALTER TABLE users ADD COLUMN phone VARCHAR(20);")
        assert "-- PR Author   : @\n" not in out
        assert "-- PR Author   : @unknown" in out

    def test_destructive_op_includes_warning(self):
        out = _wrap("ALTER TABLE users DROP COLUMN old_field;")
        assert "-- !! WARNING" in out

    def test_safe_op_has_no_warning(self):
        out = _wrap("ALTER TABLE users ADD COLUMN phone VARCHAR(20);")
        assert "-- !! WARNING" not in out

    def test_create_view_header_present(self):
        out = wrap(
            detect("CREATE VIEW v_users AS SELECT id FROM users;"),
            pr_author="lance",
        )
        assert "-- Operation   : CREATE VIEW" in out
        assert "-- PR Author   : @lance" in out

    def test_already_idempotent_has_no_header(self):
        sql = "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename='t') THEN CREATE TABLE t (id INT); END IF; END $$;"
        out = _wrap(sql)
        assert "Generated by flyway-idempotent-guardian" not in out
