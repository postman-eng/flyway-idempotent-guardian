"""
Detects DDL statement types and whether they are already idempotent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DdlType(str, Enum):
    CREATE_TABLE = "create_table"
    ADD_COLUMN = "add_column"
    ALTER_COLUMN_TYPE = "alter_column_type"
    CREATE_INDEX = "create_index"
    CREATE_UNIQUE_INDEX = "create_unique_index"
    ADD_FK = "add_fk"
    ADD_UNIQUE = "add_unique"
    ADD_CHECK = "add_check"
    DROP_COLUMN = "drop_column"
    DROP_TABLE = "drop_table"
    CREATE_TYPE = "create_type"
    ADD_NOT_NULL = "add_not_null"
    RENAME_COLUMN = "rename_column"
    DROP_CONSTRAINT = "drop_constraint"
    CREATE_VIEW = "create_view"
    CREATE_FUNCTION = "create_function"
    UNKNOWN = "unknown"


@dataclass
class DetectionResult:
    ddl_type: DdlType
    already_idempotent: bool
    # Named entities extracted from the statement
    table: Optional[str] = None
    schema: str = "public"
    column: Optional[str] = None
    new_column: Optional[str] = None   # for RENAME_COLUMN
    index: Optional[str] = None
    constraint: Optional[str] = None
    type_name: Optional[str] = None    # for CREATE_TYPE
    raw: str = ""
    dialect: str = "postgres"


# ---------------------------------------------------------------------------
# Idempotency markers — if any of these appear the SQL is already guarded
# ---------------------------------------------------------------------------
_IDEMPOTENT_MARKERS = [
    r"DO\s+\$\$",
    r"IF\s+NOT\s+EXISTS",
    r"IF\s+EXISTS",
    r"CREATE\s+OR\s+REPLACE",
    r"PREPARE\s+stmt",
]


def _already_idempotent(sql: str) -> bool:
    upper = sql.upper()
    for pattern in _IDEMPOTENT_MARKERS:
        if re.search(pattern, upper, re.IGNORECASE):
            return True
    return False


# ---------------------------------------------------------------------------
# DDL detection rules — ordered from most specific to least specific
# ---------------------------------------------------------------------------
_RULES: list[tuple[str, DdlType, dict[str, int]]] = [
    # ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY
    (
        r"ALTER\s+TABLE\s+(?:(\w+)\.)?(\w+)\s+ADD\s+CONSTRAINT\s+(\w+)\s+FOREIGN\s+KEY",
        DdlType.ADD_FK,
        {"schema": 1, "table": 2, "constraint": 3},
    ),
    # ALTER TABLE … ADD CONSTRAINT … UNIQUE
    (
        r"ALTER\s+TABLE\s+(?:(\w+)\.)?(\w+)\s+ADD\s+CONSTRAINT\s+(\w+)\s+UNIQUE",
        DdlType.ADD_UNIQUE,
        {"schema": 1, "table": 2, "constraint": 3},
    ),
    # ALTER TABLE … ADD CONSTRAINT … CHECK
    (
        r"ALTER\s+TABLE\s+(?:(\w+)\.)?(\w+)\s+ADD\s+CONSTRAINT\s+(\w+)\s+CHECK",
        DdlType.ADD_CHECK,
        {"schema": 1, "table": 2, "constraint": 3},
    ),
    # ALTER TABLE … DROP CONSTRAINT
    (
        r"ALTER\s+TABLE\s+(?:(\w+)\.)?(\w+)\s+DROP\s+CONSTRAINT\s+(\w+)",
        DdlType.DROP_CONSTRAINT,
        {"schema": 1, "table": 2, "constraint": 3},
    ),
    # ALTER TABLE … RENAME COLUMN old TO new
    (
        r"ALTER\s+TABLE\s+(?:(\w+)\.)?(\w+)\s+RENAME\s+COLUMN\s+(\w+)\s+TO\s+(\w+)",
        DdlType.RENAME_COLUMN,
        {"schema": 1, "table": 2, "column": 3, "new_column": 4},
    ),
    # ALTER TABLE … ALTER COLUMN … TYPE (PostgreSQL)
    (
        r"ALTER\s+TABLE\s+(?:(\w+)\.)?(\w+)\s+ALTER\s+COLUMN\s+(\w+)\s+(?:TYPE|SET\s+DATA\s+TYPE)",
        DdlType.ALTER_COLUMN_TYPE,
        {"schema": 1, "table": 2, "column": 3},
    ),
    # ALTER TABLE … ALTER COLUMN … SET NOT NULL
    (
        r"ALTER\s+TABLE\s+(?:(\w+)\.)?(\w+)\s+ALTER\s+COLUMN\s+(\w+)\s+SET\s+NOT\s+NULL",
        DdlType.ADD_NOT_NULL,
        {"schema": 1, "table": 2, "column": 3},
    ),
    # ALTER TABLE … MODIFY COLUMN … NOT NULL (MySQL)
    (
        r"ALTER\s+TABLE\s+(?:(\w+)\.)?(\w+)\s+MODIFY\s+COLUMN\s+(\w+)\s+.+NOT\s+NULL",
        DdlType.ADD_NOT_NULL,
        {"schema": 1, "table": 2, "column": 3},
    ),
    # ALTER TABLE … MODIFY COLUMN (MySQL type change — no NOT NULL)
    (
        r"ALTER\s+TABLE\s+(?:(\w+)\.)?(\w+)\s+MODIFY\s+COLUMN\s+(\w+)",
        DdlType.ALTER_COLUMN_TYPE,
        {"schema": 1, "table": 2, "column": 3},
    ),
    # ALTER TABLE … DROP COLUMN
    (
        r"ALTER\s+TABLE\s+(?:(\w+)\.)?(\w+)\s+DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?(\w+)",
        DdlType.DROP_COLUMN,
        {"schema": 1, "table": 2, "column": 3},
    ),
    # ALTER TABLE … ADD COLUMN
    (
        r"ALTER\s+TABLE\s+(?:(\w+)\.)?(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
        DdlType.ADD_COLUMN,
        {"schema": 1, "table": 2, "column": 3},
    ),
    # CREATE UNIQUE INDEX
    (
        r"CREATE\s+UNIQUE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+ON\s+(?:(\w+)\.)?(\w+)",
        DdlType.CREATE_UNIQUE_INDEX,
        {"index": 1, "schema": 2, "table": 3},
    ),
    # CREATE INDEX
    (
        r"CREATE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+ON\s+(?:(\w+)\.)?(\w+)",
        DdlType.CREATE_INDEX,
        {"index": 1, "schema": 2, "table": 3},
    ),
    # CREATE TABLE
    (
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:(\w+)\.)?(\w+)",
        DdlType.CREATE_TABLE,
        {"schema": 1, "table": 2},
    ),
    # DROP TABLE
    (
        r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:(\w+)\.)?(\w+)",
        DdlType.DROP_TABLE,
        {"schema": 1, "table": 2},
    ),
    # CREATE TYPE
    (
        r"CREATE\s+TYPE\s+(?:(\w+)\.)?(\w+)",
        DdlType.CREATE_TYPE,
        {"schema": 1, "type_name": 2},
    ),
    # CREATE OR REPLACE VIEW / CREATE VIEW
    (
        r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?:(\w+)\.)?(\w+)",
        DdlType.CREATE_VIEW,
        {"schema": 1, "table": 2},
    ),
    # CREATE OR REPLACE FUNCTION / CREATE FUNCTION
    (
        r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:(\w+)\.)?(\w+)",
        DdlType.CREATE_FUNCTION,
        {"schema": 1, "table": 2},
    ),
]


def detect(sql: str, dialect: str = "postgres") -> DetectionResult:
    """
    Analyse a SQL string and return a DetectionResult.
    """
    already = _already_idempotent(sql)

    for pattern, ddl_type, group_map in _RULES:
        m = re.search(pattern, sql, re.IGNORECASE | re.MULTILINE)
        if not m:
            continue

        def g(key: str) -> Optional[str]:
            idx = group_map.get(key)
            if idx is None:
                return None
            try:
                return m.group(idx)
            except IndexError:
                return None

        schema = g("schema") or "public"

        result = DetectionResult(
            ddl_type=ddl_type,
            already_idempotent=already,
            table=g("table"),
            schema=schema,
            column=g("column"),
            new_column=g("new_column"),
            index=g("index"),
            constraint=g("constraint"),
            type_name=g("type_name"),
            raw=sql.strip(),
            dialect=dialect,
        )

        # Views and functions with CREATE OR REPLACE are already idempotent
        if ddl_type in (DdlType.CREATE_VIEW, DdlType.CREATE_FUNCTION):
            if re.search(r"CREATE\s+OR\s+REPLACE", sql, re.IGNORECASE):
                result.already_idempotent = True

        # DROP TABLE IF EXISTS / CREATE TABLE IF NOT EXISTS → already safe
        if ddl_type == DdlType.DROP_TABLE and re.search(r"IF\s+EXISTS", sql, re.IGNORECASE):
            result.already_idempotent = True
        if ddl_type == DdlType.CREATE_TABLE and re.search(r"IF\s+NOT\s+EXISTS", sql, re.IGNORECASE):
            result.already_idempotent = True

        return result

    return DetectionResult(
        ddl_type=DdlType.UNKNOWN,
        already_idempotent=already,
        raw=sql.strip(),
        dialect=dialect,
    )


def detect_dialect(sql: str, hint: str = "auto") -> str:
    """
    Resolve the dialect to 'postgres' or 'mysql'.
    Priority: explicit hint > file comment > syntax heuristics > default postgres.
    """
    if hint in ("postgres", "mysql"):
        return hint

    # File-level comment: -- dialect: postgres
    m = re.search(r"--\s*dialect:\s*(postgres|mysql)", sql, re.IGNORECASE)
    if m:
        return m.group(1).lower()

    # Syntax heuristics
    if re.search(r"\bSERIAL\b|DO\s+\$\$|LANGUAGE\s+plpgsql", sql, re.IGNORECASE):
        return "postgres"
    if re.search(r"\bAUTO_INCREMENT\b|ENGINE\s*=", sql, re.IGNORECASE):
        return "mysql"

    return "postgres"
