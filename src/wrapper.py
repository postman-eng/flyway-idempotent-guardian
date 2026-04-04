"""
Generates idempotent SQL by rendering Jinja2 templates with extracted DDL context.
"""
from __future__ import annotations

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from sql_detector import DetectionResult, DdlType

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _env(dialect: str) -> Environment:
    loader = FileSystemLoader(str(_TEMPLATES_DIR / dialect))
    return Environment(loader=loader, trim_blocks=True, lstrip_blocks=True)


_DDL_TEMPLATE_MAP: dict[DdlType, str] = {
    DdlType.CREATE_TABLE: "create_table.sql.j2",
    DdlType.ADD_COLUMN: "add_column.sql.j2",
    DdlType.ALTER_COLUMN_TYPE: "alter_column_type.sql.j2",
    DdlType.CREATE_INDEX: "create_index.sql.j2",
    DdlType.CREATE_UNIQUE_INDEX: "create_index.sql.j2",
    DdlType.ADD_FK: "add_constraint.sql.j2",
    DdlType.ADD_UNIQUE: "add_constraint.sql.j2",
    DdlType.ADD_CHECK: "add_constraint.sql.j2",
    DdlType.DROP_COLUMN: "drop_column.sql.j2",
    DdlType.DROP_TABLE: "drop_table.sql.j2",
    DdlType.CREATE_TYPE: "create_type.sql.j2",
    DdlType.ADD_NOT_NULL: "add_not_null.sql.j2",
    DdlType.RENAME_COLUMN: "rename_column.sql.j2",
    DdlType.DROP_CONSTRAINT: "drop_constraint.sql.j2",
    DdlType.CREATE_VIEW: None,      # CREATE OR REPLACE VIEW is already idempotent
    DdlType.CREATE_FUNCTION: None,  # CREATE OR REPLACE FUNCTION is already idempotent
}

_CONSTRAINT_TYPE_MAP = {
    DdlType.ADD_FK: "FOREIGN KEY",
    DdlType.ADD_UNIQUE: "UNIQUE",
    DdlType.ADD_CHECK: "CHECK",
}


def wrap(result: DetectionResult) -> str:
    """
    Return idempotent SQL for the given DetectionResult.
    Returns the original SQL unchanged if it is already idempotent or unknown.
    Returns a comment placeholder if no template exists for the type.
    """
    if result.already_idempotent:
        return result.raw

    if result.ddl_type == DdlType.UNKNOWN:
        return (
            f"-- WARNING: flyway-idempotent-guardian could not classify this statement.\n"
            f"-- Manual idempotency review required.\n{result.raw}"
        )

    template_name = _DDL_TEMPLATE_MAP.get(result.ddl_type)

    # Views/functions: rewrite to use CREATE OR REPLACE
    if result.ddl_type == DdlType.CREATE_VIEW:
        return re.sub(
            r"CREATE\s+VIEW", "CREATE OR REPLACE VIEW", result.raw, count=1, flags=re.IGNORECASE
        )
    if result.ddl_type == DdlType.CREATE_FUNCTION:
        return re.sub(
            r"CREATE\s+FUNCTION", "CREATE OR REPLACE FUNCTION", result.raw, count=1, flags=re.IGNORECASE
        )

    if template_name is None:
        return (
            f"-- WARNING: No idempotency template for {result.ddl_type}.\n"
            f"-- Manual review required.\n{result.raw}"
        )

    try:
        env = _env(result.dialect)
        template = env.get_template(template_name)
    except TemplateNotFound:
        return (
            f"-- WARNING: Template '{template_name}' not found for dialect '{result.dialect}'.\n"
            f"-- Manual review required.\n{result.raw}"
        )

    ctx = {
        "schema": result.schema or "public",
        "table": result.table or "unknown_table",
        "column": result.column,
        "new_column": result.new_column,
        "index": result.index,
        "constraint": result.constraint,
        "type_name": result.type_name,
        "raw": result.raw,
        "unique": result.ddl_type == DdlType.CREATE_UNIQUE_INDEX,
        "constraint_type": _CONSTRAINT_TYPE_MAP.get(result.ddl_type, ""),
    }

    return template.render(**ctx).strip()


# Needed for the CREATE OR REPLACE rewrites above
import re  # noqa: E402 (imported after use in module body — moved here to avoid circular)
