"""Connection helpers."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

DEFAULT_DSN = "postgresql://casebook:casebook-local-only@localhost:5434/casebook"
ROOT = Path(__file__).resolve().parent.parent


def dsn() -> str:
    return os.environ.get("CASEBOOK_DSN", DEFAULT_DSN)


def connect(**kwargs) -> psycopg.Connection:
    """Autocommit connection. Cases manage their own transactions explicitly."""
    kwargs.setdefault("autocommit", True)
    kwargs.setdefault("application_name", "casebook")
    return psycopg.connect(dsn(), **kwargs)


def client_side(conn: psycopg.Connection) -> psycopg.ClientCursor:
    """A cursor that interpolates parameters client-side.

    Case queries are sent as plain literal SQL so the text that is timed, the
    text that is EXPLAINed and the text that lands in pg_stat_statements are the
    same statement, with the same custom plan.
    """
    return psycopg.ClientCursor(conn)


def scalar(conn: psycopg.Connection, sql: str, params=None):
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def split_sections(path: Path) -> dict[str, str]:
    """Split a SQL file on `-- @name` marker lines."""
    sections: dict[str, list[str]] = {}
    current = "_"
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("-- @") and " " not in stripped[4:]:
            current = stripped[4:]
            continue
        sections.setdefault(current, []).append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def statements(sql: str) -> list[str]:
    """Split simple SQL on semicolons at line ends, keeping DO blocks intact."""
    out, buf, in_dollar = [], [], False
    for line in sql.splitlines():
        if line.count("$$") % 2 == 1:
            in_dollar = not in_dollar
        buf.append(line)
        if not in_dollar and line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt and not all(l.strip().startswith("--") or not l.strip() for l in buf):
                out.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail and not all(l.strip().startswith("--") or not l.strip() for l in buf):
        out.append(tail)
    return out
