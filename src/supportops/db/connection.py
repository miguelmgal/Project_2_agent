"""SQLite connection management.

Every connection in the project goes through `connect()`. That is not stylistic
tidiness -- it is a security requirement.

**SQLite disables foreign keys by default, per connection.** A connection opened
without `PRAGMA foreign_keys = ON` silently ignores every REFERENCES clause in the
schema, so orphan rows become insertable and the structural basis of R1
(CLAUDE.md section 3) quietly disappears. Nothing fails loudly; the protection is
just gone. Centralising connection setup makes it impossible to forget in one
place and not another.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "env" / "schema.sql"
"""Single source of truth for the schema. The database itself is a derived
artifact and is git-ignored (D-010): it is rebuilt with `env/seed.py`."""

IN_MEMORY = ":memory:"


def connect(db_path: Path | str = IN_MEMORY) -> sqlite3.Connection:
    """Open a connection with the project's required settings applied.

    Args:
        db_path: file path, or `:memory:` for an ephemeral database (tests).

    Returns:
        A connection with foreign keys enforced and `sqlite3.Row` rows.
    """
    connection = sqlite3.connect(db_path)

    # See module docstring: without this, every REFERENCES clause is decoration.
    connection.execute("PRAGMA foreign_keys = ON")

    # Rows behave like mappings, so queries are read by column name rather than by
    # positional index. Positional access breaks silently when a column is added.
    connection.row_factory = sqlite3.Row

    return connection


def apply_schema(connection: sqlite3.Connection) -> None:
    """Create the schema from `env/schema.sql`.

    The script drops existing tables first, so this is idempotent: applying it to a
    populated database rebuilds it empty. That is the intended lifecycle -- the
    database is ephemeral and declarative (D-019), rebuilt rather than migrated,
    because there is no data worth preserving.
    """
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    # executescript() issues an implicit COMMIT, which resets foreign_keys on some
    # SQLite builds. Re-assert it rather than assume it survived.
    connection.execute("PRAGMA foreign_keys = ON")
    connection.commit()


def fresh_database(db_path: Path | str = IN_MEMORY) -> sqlite3.Connection:
    """Return a connection to a newly created, empty schema."""
    connection = connect(db_path)
    apply_schema(connection)
    return connection


def foreign_keys_enabled(connection: sqlite3.Connection) -> bool:
    """Whether this connection actually enforces foreign keys.

    Exposed so tests can assert it directly. A silently-disabled pragma is the
    failure mode this module exists to prevent, so it must be observable.
    """
    row = connection.execute("PRAGMA foreign_keys").fetchone()
    return bool(row[0])


def allowed_check_values(connection: sqlite3.Connection, table: str, column: str) -> set[str]:
    """Extract the literal values a column's CHECK constraint permits.

    Reads them out of the stored DDL rather than duplicating them in Python. That
    matters for the schema-to-golden-set coverage test: if the allowed values were
    hardcoded in two places they would drift, and the coverage test would be
    checking a stale list instead of the real domain.
    """
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if row is None:
        msg = f"Table {table!r} does not exist."
        raise ValueError(msg)

    ddl: str = row["sql"]

    # Locate `<column> IN (` allowing any whitespace. `\s+IN` deliberately does not
    # match `status NOT IN (...)`, so the enum definition is found rather than one of
    # the business-invariant constraints that also mention the column.
    match = re.search(rf"\b{re.escape(column)}\s+IN\s*\(", ddl, re.IGNORECASE)
    if match is None:
        msg = f"No CHECK ... IN (...) constraint found for {table}.{column}."
        raise ValueError(msg)

    # Balance parentheses instead of searching for the first ')': a value could
    # contain one, and the enclosing CHECK adds its own.
    depth = 1
    index = match.end()
    while index < len(ddl) and depth > 0:
        if ddl[index] == "(":
            depth += 1
        elif ddl[index] == ")":
            depth -= 1
        index += 1
    fragment = ddl[match.end() : index - 1]

    # Strip SQL line comments before parsing: the schema documents each value
    # inline, e.g. "'pending',  -- paid, untouched".
    values: set[str] = set()
    for raw_line in fragment.splitlines():
        line = raw_line.split("--", 1)[0]
        for token in line.split(","):
            candidate = token.strip().strip("'").strip()
            if candidate:
                values.add(candidate)
    return values


def iter_tables(connection: sqlite3.Connection) -> Iterator[str]:
    """Yield the names of real tables, skipping SQLite internals and FTS shadows."""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'faq_search%' "
        "ORDER BY name"
    ).fetchall()
    for row in rows:
        yield str(row["name"])
