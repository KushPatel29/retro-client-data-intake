"""Guarded, read-only SQL for anything typed by a person or written by an AI model.

Guardrails: one statement only, SELECT/WITH only, read-only connection, no file or network access
from SQL, and a row limit. A query that fails any check is refused with the reason.
"""
from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

MAX_ROWS = 500
BLOCKED = re.compile(r"\b(read_csv|read_parquet|read_json|copy|attach|install|load|pragma|export|import|"
                     r"httpfs|glob|getenv|read_text|read_blob)\b", re.IGNORECASE)


class Refused(ValueError):
    pass


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(db_path), read_only=True)
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def check(sql: str) -> str:
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise Refused("The query is empty.")
    try:
        statements = duckdb.extract_statements(text)
    except duckdb.Error as e:
        raise Refused(f"The query does not parse: {e}") from None
    if len(statements) != 1:
        raise Refused("Only one statement at a time.")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise Refused("Only SELECT queries are allowed; nothing can be changed from here.")
    if BLOCKED.search(text):
        raise Refused("File, extension and settings functions are blocked.")
    return text


def run(con: duckdb.DuckDBPyConnection, sql: str, limit: int = MAX_ROWS) -> pd.DataFrame:
    text = check(sql)
    return con.execute(f"SELECT * FROM ({text}) AS q LIMIT {int(limit)}").df()


def schema(con: duckdb.DuckDBPyConnection) -> str:
    """Tables and columns, as compact text for a model prompt or a help panel."""
    df = con.execute("""SELECT table_name, string_agg(column_name || ' ' || data_type, ', ' ORDER BY ordinal_position) cols
                        FROM information_schema.columns WHERE table_schema = 'main'
                        GROUP BY table_name ORDER BY table_name""").df()
    return "\n".join(f"{r.table_name}({r.cols})" for r in df.itertuples())
