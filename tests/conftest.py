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
from env.seed import insert_anchors, insert_filler, load_knowledge_base
from faker import Faker

from supportops.config import get_settings
from supportops.db import connect
from supportops.db.connection import apply_schema
from tests.builders import make_customer, make_mechanism_faqs, make_order

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

    Minimal, for schema-invariant tests that only need something valid to violate.
    """
    make_customer(db)
    make_order(db)
    return db


@pytest.fixture
def seeded_full_db(db: sqlite3.Connection) -> sqlite3.Connection:
    """The real dataset: anchors plus Faker filler, in memory.

    Repository tests need the full set for two reasons:

    1. **The anchors are the cases.** "Maria has exactly three orders", "Carlos owns
       ORD-2001", "ORD-1002 is delivered but overdue" -- the assertions reference them
       by name, which is what makes anchors anchors.
    2. **The filler gives the customer filter teeth.** With only one customer present,
       a repository bug that forgot `WHERE customer_id = ?` would still return the
       "right" count, because that customer's orders would be all the orders. With 100
       orders, expected 3 versus actual 100 is impossible to miss.

    Built in memory per test, so tests that write tickets cannot contaminate each other.
    """
    insert_anchors(db)
    insert_filler(db, Faker("es_ES"), get_settings().faker_seed)
    load_knowledge_base(db)
    return db


@pytest.fixture
def db_with_faq(seeded_full_db: sqlite3.Connection) -> sqlite3.Connection:
    """The full dataset plus three synthetic FAQ articles.

    For repository *mechanism* tests only. The articles live in `tests/builders.py`
    with disjoint vocabularies, so a ranking assertion cannot pass by luck, and they
    are independent of the real knowledge base -- rewriting a real article must never
    break a test of the JOIN.
    """
    make_mechanism_faqs(seeded_full_db)
    return seeded_full_db
