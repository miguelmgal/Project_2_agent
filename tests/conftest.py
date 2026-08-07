"""Shared pytest fixtures.

Isolation rule (CLAUDE.md section 7): every test gets its own database. Tests that
mutate state -- and `create_ticket` does -- must not contaminate each other, because
the resulting failures depend on execution order and are miserable to debug.

Row builders live in `tests/builders.py`, not here: conftest is for dependency
injection, not a utility library.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from supportops.db import connect
from supportops.db.connection import apply_schema
from tests.builders import make_customer, make_order

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    """An empty, isolated in-memory database with the schema applied.

    In-memory rather than a temp file: faster, and it cannot leak between tests even
    by accident, since the database ceases to exist along with the connection.
    """
    connection = connect()
    apply_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def seeded_db(db: sqlite3.Connection) -> sqlite3.Connection:
    """A database holding one customer and one shipped order.

    The common starting point for repository and tool tests.
    """
    make_customer(db)
    make_order(db)
    return db
