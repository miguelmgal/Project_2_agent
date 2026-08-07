"""Row builders for tests.

Each builder inserts a **valid** row and lets a test override only the fields it
cares about. That keeps a test's intent visible in its overrides instead of buried
in ten positional arguments, and means an invariant test reads as a single
deliberate violation:

    make_order(db, tracking_number=None)   # shipped, but no tracking -> must fail

Kept out of `conftest.py` on purpose: conftest is for fixtures (dependency
injection), not a utility library. Mixing the two makes conftest grow into a
grab-bag that every test file imports from for unrelated reasons.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

# Shared anchors, so tests refer to the same entities as the seeded fixtures.
CUSTOMER_ID = "CUST-0007"
CUSTOMER_EMAIL = "maria.lopez@example.com"
ORDER_ID = "ORD-1042"


def make_customer(db: sqlite3.Connection, **overrides: Any) -> str:
    """Insert a valid customer and return its id."""
    row: dict[str, Any] = {
        "id": CUSTOMER_ID,
        "email": CUSTOMER_EMAIL,
        "full_name": "Maria Lopez",
        "tier": "standard",
        "created_at": "2024-03-15T10:22:00Z",
    }
    row.update(overrides)
    db.execute(
        "INSERT INTO customers (id, email, full_name, tier, created_at) "
        "VALUES (:id, :email, :full_name, :tier, :created_at)",
        row,
    )
    db.commit()
    return str(row["id"])


def make_order(db: sqlite3.Connection, **overrides: Any) -> str:
    """Insert a valid order and return its id.

    Defaults describe a coherent *shipped* order: dispatched, with carrier and
    tracking, promised delivery after creation.
    """
    row: dict[str, Any] = {
        "id": ORDER_ID,
        "customer_id": CUSTOMER_ID,
        "status": "shipped",
        "total_amount_cents": 4599,
        "currency": "USD",
        "created_at": "2026-08-01T09:00:00Z",
        "estimated_delivery": "2026-08-10T00:00:00Z",
        "shipped_at": "2026-08-02T11:00:00Z",
        "carrier": "DHL",
        "tracking_number": "DHL8891234567",
    }
    row.update(overrides)
    db.execute(
        "INSERT INTO orders (id, customer_id, status, total_amount_cents, currency, "
        "created_at, estimated_delivery, shipped_at, carrier, tracking_number) "
        "VALUES (:id, :customer_id, :status, :total_amount_cents, :currency, "
        ":created_at, :estimated_delivery, :shipped_at, :carrier, :tracking_number)",
        row,
    )
    db.commit()
    return str(row["id"])


def make_ticket(db: sqlite3.Connection, **overrides: Any) -> str:
    """Insert a valid, non-escalated ticket and return its id."""
    row: dict[str, Any] = {
        "id": "TCK-0001",
        "customer_id": CUSTOMER_ID,
        "summary": "Customer asks where order ORD-1042 is",
        "priority": "medium",
        "status": "open",
        "escalated": 0,
        "escalation_category": None,
        "escalation_reason": None,
        "created_at": "2026-08-07T10:00:00Z",
    }
    row.update(overrides)
    db.execute(
        "INSERT INTO tickets (id, customer_id, summary, priority, status, escalated, "
        "escalation_category, escalation_reason, created_at) "
        "VALUES (:id, :customer_id, :summary, :priority, :status, :escalated, "
        ":escalation_category, :escalation_reason, :created_at)",
        row,
    )
    db.commit()
    return str(row["id"])


def make_faq(db: sqlite3.Connection, **overrides: Any) -> int:
    """Insert a valid FAQ article and return its id."""
    row: dict[str, Any] = {
        "id": 1,
        "slug": "password-reset",
        "title": "Como restablecer tu contrasena",
        "body": "Entra en Ajustes, pulsa 'Olvide mi contrasena' y sigue el enlace.",
        "category": "account",
    }
    row.update(overrides)
    db.execute(
        "INSERT INTO faq_articles (id, slug, title, body, category) "
        "VALUES (:id, :slug, :title, :body, :category)",
        row,
    )
    db.commit()
    return int(row["id"])
