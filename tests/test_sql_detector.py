"""Tests for sql_detector.py"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sql_detector import DdlType, detect, detect_dialect


# ---------------------------------------------------------------------------
# Dialect detection
# ---------------------------------------------------------------------------

class TestDetectDialect:
    def test_explicit_postgres(self):
        assert detect_dialect("SELECT 1", "postgres") == "postgres"

    def test_explicit_mysql(self):
        assert detect_dialect("SELECT 1", "mysql") == "mysql"

    def test_file_comment_postgres(self):
        sql = "-- dialect: postgres\nCREATE TABLE foo (id INT);"
        assert detect_dialect(sql, "auto") == "postgres"

    def test_file_comment_mysql(self):
        sql = "-- dialect: mysql\nCREATE TABLE foo (id INT);"
        assert detect_dialect(sql, "auto") == "mysql"

    def test_serial_heuristic(self):
        sql = "CREATE TABLE t (id SERIAL PRIMARY KEY);"
        assert detect_dialect(sql, "auto") == "postgres"

    def test_auto_increment_heuristic(self):
        sql = "CREATE TABLE t (id INT AUTO_INCREMENT PRIMARY KEY);"
        assert detect_dialect(sql, "auto") == "mysql"

    def test_default_postgres(self):
        assert detect_dialect("CREATE TABLE t (id INT);", "auto") == "postgres"


# ---------------------------------------------------------------------------
# DDL type detection — PostgreSQL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql,expected_type,expected_table", [
    (
        "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);",
        DdlType.CREATE_TABLE, "users",
    ),
    (
        "ALTER TABLE users ADD COLUMN phone VARCHAR(20);",
        DdlType.ADD_COLUMN, "users",
    ),
    (
        "ALTER TABLE users ALTER COLUMN email TYPE TEXT;",
        DdlType.ALTER_COLUMN_TYPE, "users",
    ),
    (
        "CREATE INDEX idx_users_email ON users(email);",
        DdlType.CREATE_INDEX, "users",
    ),
    (
        "CREATE UNIQUE INDEX idx_users_email_unique ON users(email);",
        DdlType.CREATE_UNIQUE_INDEX, "users",
    ),
    (
        "ALTER TABLE orders ADD CONSTRAINT fk_orders_user_id FOREIGN KEY (user_id) REFERENCES users(id);",
        DdlType.ADD_FK, "orders",
    ),
    (
        "ALTER TABLE users ADD CONSTRAINT uq_users_email UNIQUE (email);",
        DdlType.ADD_UNIQUE, "users",
    ),
    (
        "ALTER TABLE users ADD CONSTRAINT chk_email_format CHECK (email LIKE '%@%');",
        DdlType.ADD_CHECK, "users",
    ),
    (
        "ALTER TABLE users DROP COLUMN obsolete_field;",
        DdlType.DROP_COLUMN, "users",
    ),
    (
        "DROP TABLE obsolete_table CASCADE;",
        DdlType.DROP_TABLE, "obsolete_table",
    ),
    (
        "CREATE TYPE user_status AS ENUM ('active', 'inactive');",
        DdlType.CREATE_TYPE, None,  # table is None for CREATE TYPE
    ),
    (
        "ALTER TABLE users ALTER COLUMN email SET NOT NULL;",
        DdlType.ADD_NOT_NULL, "users",
    ),
    (
        "ALTER TABLE users RENAME COLUMN old_name TO new_name;",
        DdlType.RENAME_COLUMN, "users",
    ),
    (
        "ALTER TABLE users DROP CONSTRAINT old_constraint;",
        DdlType.DROP_CONSTRAINT, "users",
    ),
    (
        "CREATE VIEW v_active_users AS SELECT id FROM users;",
        DdlType.CREATE_VIEW, "v_active_users",
    ),
    (
        "CREATE FUNCTION get_count() RETURNS INTEGER AS $$ BEGIN RETURN 1; END; $$ LANGUAGE plpgsql;",
        DdlType.CREATE_FUNCTION, "get_count",
    ),
])
def test_postgres_ddl_type(sql, expected_type, expected_table):
    result = detect(sql, "postgres")
    assert result.ddl_type == expected_type
    if expected_table is not None:
        assert result.table == expected_table or result.type_name == expected_table


# ---------------------------------------------------------------------------
# Idempotency detection
# ---------------------------------------------------------------------------

class TestAlreadyIdempotent:
    def test_do_block_is_idempotent(self):
        sql = "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_tables WHERE tablename='t') THEN CREATE TABLE t (id INT); END IF; END $$;"
        result = detect(sql, "postgres")
        assert result.already_idempotent is True

    def test_bare_create_table_not_idempotent(self):
        result = detect("CREATE TABLE users (id SERIAL PRIMARY KEY);", "postgres")
        assert result.already_idempotent is False

    def test_create_table_if_not_exists_is_idempotent(self):
        result = detect("CREATE TABLE IF NOT EXISTS users (id INT);", "postgres")
        assert result.already_idempotent is True

    def test_drop_table_if_exists_is_idempotent(self):
        result = detect("DROP TABLE IF EXISTS obsolete;", "postgres")
        assert result.already_idempotent is True

    def test_create_or_replace_view_is_idempotent(self):
        result = detect("CREATE OR REPLACE VIEW v AS SELECT 1;", "postgres")
        assert result.already_idempotent is True

    def test_mysql_prepare_pattern_is_idempotent(self):
        sql = "PREPARE stmt FROM @query; EXECUTE stmt; DEALLOCATE PREPARE stmt;"
        result = detect(sql, "mysql")
        assert result.already_idempotent is True


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

class TestEntityExtraction:
    def test_add_column_extracts_column(self):
        result = detect("ALTER TABLE users ADD COLUMN phone VARCHAR(20);", "postgres")
        assert result.column == "phone"
        assert result.table == "users"

    def test_create_index_extracts_index_name(self):
        result = detect("CREATE INDEX idx_users_email ON users(email);", "postgres")
        assert result.index == "idx_users_email"
        assert result.table == "users"

    def test_add_fk_extracts_constraint(self):
        result = detect(
            "ALTER TABLE orders ADD CONSTRAINT fk_orders_user_id FOREIGN KEY (user_id) REFERENCES users(id);",
            "postgres",
        )
        assert result.constraint == "fk_orders_user_id"
        assert result.table == "orders"

    def test_rename_column_extracts_both_names(self):
        result = detect("ALTER TABLE users RENAME COLUMN old_name TO new_name;", "postgres")
        assert result.column == "old_name"
        assert result.new_column == "new_name"

    def test_schema_qualified_table(self):
        result = detect("CREATE TABLE myschema.users (id INT);", "postgres")
        assert result.schema == "myschema"
        assert result.table == "users"

    def test_default_schema_is_public(self):
        result = detect("CREATE TABLE users (id INT);", "postgres")
        assert result.schema == "public"
