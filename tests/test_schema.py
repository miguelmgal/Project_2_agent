"""Schema invariant tests.

Deterministic, no LLM, runs on every PR (CLAUDE.md section 7, "unit" level).

These are **invariant** tests, not snapshot tests. They assert what must stay true,
not what the DDL literally looks like. Consequences:

  * Adding a column, reordering columns or fixing a comment keeps them green --
    those changes break no property.
  * Deleting a CHECK constraint turns one red, and it cannot be fixed by
    regenerating anything. The only options are restoring the constraint or
    deleting the test, and deleting a test is a visible, reviewable decision.

That asymmetry is the whole point. A test whose fix is "regenerate" gets fixed
reflexively, without anyone reading the diff.

Why bother testing the database at all: these constraints are what let the agent
trust what it reads. If a nonsensical order cannot exist, then a bad answer is the
agent's fault rather than corrupt data -- and that distinction is what makes
evaluation results interpretable instead of a two-hour investigation each time.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from supportops.db.connection import (
    allowed_check_values,
    foreign_keys_enabled,
    iter_tables,
)
from tests.builders import make_customer, make_faq, make_order, make_ticket

if TYPE_CHECKING:
    from collections.abc import Callable


# ------------------------------------------------------------------ connection setup


class TestConnectionSetup:
    """Guards against the pragma that fails silently."""

    def test_foreign_keys_are_enforced(self, db: sqlite3.Connection) -> None:
        """SQLite disables foreign keys by default, per connection.

        If this regresses, no test fails loudly and no error is raised -- every
        REFERENCES clause simply stops being enforced and orphan rows become
        insertable. This is the only test that catches that, and R1's structural
        guarantee depends on it.
        """
        assert foreign_keys_enabled(db) is True

    def test_all_tables_are_strict(self, db: sqlite3.Connection) -> None:
        """STRICT is what makes SQLite reject a wrong-typed value.

        Without it, storing the string 'banana' in an INTEGER money column
        succeeds. Asserted per table so a future table cannot be added without it.
        """
        for table in iter_tables(db):
            row = db.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            assert "STRICT" in row["sql"].upper(), f"table {table} is not STRICT"


# ----------------------------------------------------------------- type enforcement


class TestTypeEnforcement:
    def test_money_column_rejects_text(self, seeded_db: sqlite3.Connection) -> None:
        """Money is INTEGER minor units, and STRICT enforces it."""
        with pytest.raises(sqlite3.IntegrityError):
            make_order(seeded_db, id="ORD-BAD", total_amount_cents="banana")

    def test_money_must_be_positive(self, seeded_db: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            make_order(seeded_db, id="ORD-BAD", total_amount_cents=0)

    @pytest.mark.parametrize(
        "bad_timestamp",
        [
            "15/03/2024",  # wrong format entirely
            "2024-03-15",  # date only, no time
            "2024-03-15 10:22:00",  # space instead of T, no Z
            "2024-03-15T10:22:00+02:00",  # offset instead of UTC
        ],
    )
    def test_timestamps_must_be_iso8601_utc(
        self, db: sqlite3.Connection, bad_timestamp: str
    ) -> None:
        """ISO-8601 UTC is what makes timestamps sort lexicographically.

        A mixed-format column silently breaks every ORDER BY and every date
        comparison, including the "is this order late?" logic the agent depends on.
        """
        with pytest.raises(sqlite3.IntegrityError):
            make_customer(db, created_at=bad_timestamp)

    def test_email_must_be_stored_normalised(self, db: sqlite3.Connection) -> None:
        """Email is the authentication key, so it cannot have two spellings.

        If 'Maria@Example.com' and 'maria@example.com' could both exist, the
        AUTHENTICATE node could resolve the same person to two different customer
        ids -- and identity resolution is the foundation of R1.
        """
        with pytest.raises(sqlite3.IntegrityError):
            make_customer(db, email="Maria.Lopez@Example.com")

    def test_email_must_be_unique(self, db: sqlite3.Connection) -> None:
        make_customer(db, id="CUST-0001", email="dup@example.com")
        with pytest.raises(sqlite3.IntegrityError):
            make_customer(db, id="CUST-0002", email="dup@example.com")


# ---------------------------------------------------------- referential integrity


class TestReferentialIntegrity:
    """The structural basis of R1: rows cannot reference customers that do not exist."""

    def test_order_cannot_reference_missing_customer(self, db: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            make_order(db, customer_id="CUST-DOES-NOT-EXIST")

    def test_ticket_cannot_reference_missing_customer(self, db: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            make_ticket(db, customer_id="CUST-DOES-NOT-EXIST")

    def test_customer_with_orders_cannot_be_deleted(self, seeded_db: sqlite3.Connection) -> None:
        """ON DELETE RESTRICT: deleting a customer must not orphan their orders."""
        with pytest.raises(sqlite3.IntegrityError):
            seeded_db.execute("DELETE FROM customers WHERE id = 'CUST-0007'")


# --------------------------------------------------------- order business invariants


class TestOrderInvariants:
    """A nonsensical order must be impossible to store.

    This is what lets the agent trust what it reads: when it sees a shipped order,
    that order is guaranteed to have a tracking number to hand over.
    """

    def test_shipped_order_requires_tracking(self, seeded_db: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            make_order(seeded_db, id="ORD-BAD", tracking_number=None)

    def test_shipped_order_requires_carrier(self, seeded_db: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            make_order(seeded_db, id="ORD-BAD", carrier=None)

    @pytest.mark.parametrize("status", ["shipped", "delivered", "returned"])
    def test_dispatched_statuses_require_a_ship_date(
        self, seeded_db: sqlite3.Connection, status: str
    ) -> None:
        """These statuses mean the parcel left the warehouse."""
        with pytest.raises(sqlite3.IntegrityError):
            make_order(
                seeded_db,
                id="ORD-BAD",
                status=status,
                shipped_at=None,
                carrier=None,
                tracking_number=None,
            )

    @pytest.mark.parametrize("status", ["pending", "processing", "cancelled"])
    def test_undispatched_statuses_reject_a_ship_date(
        self, seeded_db: sqlite3.Connection, status: str
    ) -> None:
        """These mean it never left, so shipping details are a contradiction."""
        with pytest.raises(sqlite3.IntegrityError):
            make_order(seeded_db, id="ORD-BAD", status=status)

    def test_delayed_may_be_dispatched_or_not(self, seeded_db: sqlite3.Connection) -> None:
        """'delayed' is deliberately unconstrained on dispatch.

        An order can be late in the warehouse or late in transit. The ambiguity is
        realistic and makes for a richer test case than a status pinned to exactly
        one situation -- so both shapes must be storable.
        """
        make_order(seeded_db, id="ORD-LATE-TRANSIT", status="delayed")
        make_order(
            seeded_db,
            id="ORD-LATE-WAREHOUSE",
            status="delayed",
            shipped_at=None,
            carrier=None,
            tracking_number=None,
        )
        count = seeded_db.execute(
            "SELECT count(*) AS n FROM orders WHERE status = 'delayed'"
        ).fetchone()["n"]
        assert count == 2

    def test_cannot_ship_before_the_order_exists(self, seeded_db: sqlite3.Connection) -> None:
        """Time only moves forward."""
        with pytest.raises(sqlite3.IntegrityError):
            make_order(
                seeded_db,
                id="ORD-BAD",
                created_at="2026-08-05T09:00:00Z",
                shipped_at="2026-08-01T09:00:00Z",
            )

    def test_status_typo_is_rejected(self, seeded_db: sqlite3.Connection) -> None:
        """'shiped' must not be storable.

        Without this constraint the typo persists silently and the agent answers
        wrongly because of a corrupt row -- the failure mode hardest to attribute.
        """
        with pytest.raises(sqlite3.IntegrityError):
            make_order(seeded_db, id="ORD-BAD", status="shiped")


# -------------------------------------------------------- ticket business invariants


class TestTicketInvariants:
    """Escalation must always be measurable.

    escalation_category is a reported metric, so an escalation without one is an
    unmeasurable escalation. The database refuses to record one -- the constraint
    protects the ability to measure, not the tidiness of the data.
    """

    def test_escalated_ticket_requires_a_category(self, seeded_db: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            make_ticket(
                seeded_db,
                status="escalated",
                escalated=1,
                escalation_category=None,
                escalation_reason="Customer is angry",
            )

    def test_escalated_ticket_requires_a_reason(self, seeded_db: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            make_ticket(
                seeded_db,
                status="escalated",
                escalated=1,
                escalation_category="angry_customer",
                escalation_reason=None,
            )

    def test_non_escalated_ticket_rejects_a_category(self, seeded_db: sqlite3.Connection) -> None:
        """The biconditional runs both ways: no category without escalation either."""
        with pytest.raises(sqlite3.IntegrityError):
            make_ticket(seeded_db, escalation_category="angry_customer")

    def test_escalated_flag_and_status_cannot_disagree(self, seeded_db: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            make_ticket(
                seeded_db,
                status="open",
                escalated=1,
                escalation_category="refund_request",
                escalation_reason="Customer demands a refund",
            )

    def test_escalated_status_requires_the_flag(self, seeded_db: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            make_ticket(seeded_db, status="escalated", escalated=0)

    def test_coherent_escalated_ticket_is_accepted(self, seeded_db: sqlite3.Connection) -> None:
        """The positive case: all the negatives above would be vacuous without it.

        A constraint that rejects everything also passes every rejection test.
        """
        make_ticket(
            seeded_db,
            status="escalated",
            escalated=1,
            escalation_category="refund_request",
            escalation_reason="Order delayed and customer demands a refund",
        )
        row = seeded_db.execute("SELECT * FROM tickets WHERE id = 'TCK-0001'").fetchone()
        assert row["escalated"] == 1
        assert row["escalation_category"] == "refund_request"

    def test_summary_must_be_substantive(self, seeded_db: sqlite3.Connection) -> None:
        """Blocks an agent writing 'ok' as a ticket summary."""
        with pytest.raises(sqlite3.IntegrityError):
            make_ticket(seeded_db, summary="ok")


# ------------------------------------------------------------ knowledge base + FTS5


class TestKnowledgeBase:
    def test_faq_needs_no_customer(self, db: sqlite3.Connection) -> None:
        """Articles belong to nobody.

        This is why search_knowledge_base is the one tool that needs no injected
        identity: there is nothing to scope.
        """
        make_faq(db)
        row = db.execute("SELECT count(*) AS n FROM faq_articles").fetchone()
        assert row["n"] == 1

    def test_fts_index_is_populated_by_trigger(self, db: sqlite3.Connection) -> None:
        """The classic FTS5 mistake is an index that silently goes stale.

        The triggers are the fix, and this is what proves they fire.
        """
        make_faq(db)
        hits = db.execute(
            "SELECT slug FROM faq_search JOIN faq_articles ON faq_articles.id = faq_search.rowid "
            "WHERE faq_search MATCH 'contrasena'"
        ).fetchall()
        assert [h["slug"] for h in hits] == ["password-reset"]

    def test_fts_search_ignores_accents(self, db: sqlite3.Connection) -> None:
        """remove_diacritics 2: customers do not type accents in support tickets.

        'contrasena' must find an article titled 'contraseña'.
        """
        make_faq(db, title="Como restablecer tu contraseña")
        hits = db.execute("SELECT rowid FROM faq_search WHERE faq_search MATCH 'contrasena'")
        assert len(hits.fetchall()) == 1

    def test_fts_index_follows_deletes(self, db: sqlite3.Connection) -> None:
        """A deleted article must stop being findable, or the agent cites a ghost."""
        make_faq(db)
        db.execute("DELETE FROM faq_articles WHERE slug = 'password-reset'")
        db.commit()
        hits = db.execute("SELECT rowid FROM faq_search WHERE faq_search MATCH 'contrasena'")
        assert hits.fetchall() == []

    def test_slug_must_be_url_safe(self, db: sqlite3.Connection) -> None:
        """Slugs map to filenames in env/knowledge_base/."""
        with pytest.raises(sqlite3.IntegrityError):
            make_faq(db, slug="Password Reset!")


# ------------------------------------------------------------------- domain inventory


class TestDomainInventory:
    """The allowed enum values, read from the schema rather than hardcoded.

    These do not pin a frozen list. They assert the domain is readable from the DDL,
    which is what the schema-to-golden-set coverage test will rely on once the golden
    set exists (end of Phase 1): every status the schema permits must have at least
    one golden-set ticket exercising it, or it is untested surface.

    Reading the values from the DDL rather than duplicating them in Python is
    deliberate -- two copies drift, and a coverage test checking a stale list is
    worse than no coverage test.
    """

    def test_order_statuses_are_readable_from_the_schema(self, db: sqlite3.Connection) -> None:
        statuses = allowed_check_values(db, "orders", "status")
        assert statuses == {
            "pending",
            "processing",
            "shipped",
            "delivered",
            "delayed",
            "cancelled",
            "returned",
        }

    def test_ticket_priorities_are_readable_from_the_schema(self, db: sqlite3.Connection) -> None:
        assert allowed_check_values(db, "tickets", "priority") == {
            "low",
            "medium",
            "high",
            "urgent",
        }

    def test_customer_tiers_are_readable_from_the_schema(self, db: sqlite3.Connection) -> None:
        assert allowed_check_values(db, "customers", "tier") == {"standard", "premium"}

    @pytest.mark.parametrize(
        ("table", "column", "inserter"),
        [
            ("orders", "status", make_order),
            ("customers", "tier", make_customer),
        ],
    )
    def test_every_allowed_value_is_actually_insertable(
        self,
        seeded_db: sqlite3.Connection,
        table: str,
        column: str,
        inserter: Callable[..., object],
    ) -> None:
        """A value the CHECK permits but that no valid row can carry is a trap.

        It would look like a supported state while being unreachable, so the golden
        set could never cover it.
        """
        for index, value in enumerate(sorted(allowed_check_values(seeded_db, table, column))):
            kwargs: dict[str, object] = {column: value}
            if table == "orders":
                kwargs["id"] = f"ORD-INV-{index}"
                # Undispatched statuses must not carry shipping details.
                if value in {"pending", "processing", "cancelled"}:
                    kwargs |= {"shipped_at": None, "carrier": None, "tracking_number": None}
            else:
                kwargs |= {"id": f"CUST-INV-{index}", "email": f"inv{index}@example.com"}
            inserter(seeded_db, **kwargs)
