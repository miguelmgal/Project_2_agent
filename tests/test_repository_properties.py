"""Repository property tests (D-024).

Deterministic in the sense that matters -- no LLM, no network, no cost -- so these run
on every PR alongside the example-based tests.

**Why both kinds exist.** `test_repository.py` documents concrete business cases in a
form anyone can read: "CUST-0001 cannot see ORD-2001". It proves *one* combination.
The guarantee R1 actually needs is universal:

    for ANY customer and ANY order, the repository returns the order only if it
    genuinely belongs to that customer

That is what a property test states, and Hypothesis then hunts for a counterexample --
here across 30 customers x 100 orders, plus arbitrary text for the adversarial cases.
When it finds one it *shrinks* it: the failing input is reduced to the smallest form
that still fails, so the report reads `order_id=''` instead of some unreadable blob.

The difference is what you can claim in a review: "it does not leak in the cases I
thought of" versus **"it does not leak in any combination"**.

**Where Hypothesis earns its keep over the anchors.** The apostrophe anchor
(`Beatriz O'Donnell`) is permanent presence -- every run touching names exercises it.
Hypothesis is active search: NUL bytes, emoji, 10k-character strings, text nobody
would write by hand. Presence and search are different kinds of coverage, and the
adversarial properties below are the search half.

**Performance note.** Hypothesis runs ~100 examples per test. Rebuilding the database
per example would cost seconds per test, and pytest's function-scoped fixtures are not
recreated between examples anyway (Hypothesis emits a health-check warning for exactly
that reason). So read-only properties share one database built at import time -- reads
cannot contaminate each other -- while the one write property builds its own inside the
test body.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import TYPE_CHECKING

from env.anchors import REFERENCE_DATE
from faker import Faker
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from supportops.config import get_settings
from supportops.db import CustomerScopedRepository, KnowledgeRepository, connect
from supportops.db.connection import apply_schema
from supportops.db.models import OrderStatus, TicketPriority, parse_timestamp

if TYPE_CHECKING:
    from collections.abc import Sequence

NOW = parse_timestamp(REFERENCE_DATE)

# Hypothesis defaults to a 200ms per-example deadline. Database work occasionally
# exceeds it on a cold cache, which would fail the test for being slow rather than for
# being wrong. Disabled deliberately: these assert correctness, not latency.
PROPERTY_SETTINGS = settings(
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@cache
def _readonly_db() -> sqlite3.Connection:
    """One database shared by every read-only property.

    Built once at first use. Safe to share because none of the properties using it
    write anything -- and the write property below deliberately does not use it.
    """
    db = connect()
    apply_schema(db)
    from env.seed import insert_anchors, insert_filler

    insert_anchors(db)
    insert_filler(db, Faker("es_ES"), get_settings().faker_seed)
    return db


@cache
def _all_customer_ids() -> Sequence[str]:
    rows = _readonly_db().execute("SELECT id FROM customers ORDER BY id").fetchall()
    return [row["id"] for row in rows]


@cache
def _all_order_ids() -> Sequence[str]:
    rows = _readonly_db().execute("SELECT id FROM orders ORDER BY id").fetchall()
    return [row["id"] for row in rows]


def _customer_ids() -> st.SearchStrategy[str]:
    return st.sampled_from(list(_all_customer_ids()))


def _order_ids() -> st.SearchStrategy[str]:
    return st.sampled_from(list(_all_order_ids()))


def _scoped(customer_id: str, now: datetime = NOW) -> CustomerScopedRepository:
    return CustomerScopedRepository(_readonly_db(), customer_id=customer_id, now=now)


# ---------------------------------------------------------------- R1, universally


class TestIsolationIsUniversal:
    """R1 as a property rather than a handful of examples."""

    @PROPERTY_SETTINGS
    @given(customer_id=_customer_ids(), order_id=_order_ids())
    def test_a_returned_order_always_belongs_to_the_caller(
        self, customer_id: str, order_id: str
    ) -> None:
        """The central guarantee of the project, over every real pairing.

        30 customers x 100 orders = 3,000 combinations. Any single one that returned
        another customer's order would be a data breach, so "I tested the ones I
        thought of" is not a strong enough claim.
        """
        order = _scoped(customer_id).get_order(order_id=order_id)
        if order is not None:
            assert order.customer_id == customer_id

    @PROPERTY_SETTINGS
    @given(customer_id=_customer_ids())
    def test_listing_is_always_scoped(self, customer_id: str) -> None:
        """No customer's listing ever contains someone else's order."""
        assert all(o.customer_id == customer_id for o in _scoped(customer_id).list_orders())

    @PROPERTY_SETTINGS
    @given(customer_id=_customer_ids())
    def test_listing_matches_a_direct_count(self, customer_id: str) -> None:
        """Cross-checks the filter against an independent query.

        Catches the failure the example tests would miss: a repository returning *all*
        orders would still satisfy "every order belongs to the caller" for whichever
        customer happens to own them. Comparing against a separately-written COUNT
        makes the two paths agree or fail.
        """
        expected = (
            _readonly_db()
            .execute("SELECT count(*) AS n FROM orders WHERE customer_id = ?", (customer_id,))
            .fetchone()["n"]
        )
        assert len(_scoped(customer_id).list_orders()) == expected


class TestAdversarialInput:
    """Arbitrary text where a real identifier is expected.

    This is the half of coverage the anchors cannot provide: NUL bytes, emoji,
    thousand-character strings, control characters. Nobody writes these by hand, and a
    prompt-injected ticket can absolutely produce them.
    """

    @PROPERTY_SETTINGS
    @given(customer_id=_customer_ids(), order_id=st.text(max_size=200))
    def test_arbitrary_order_ids_never_leak_or_raise(self, customer_id: str, order_id: str) -> None:
        """Any string as an order id: either nothing, or something owned.

        Two properties at once, and both matter. Not raising means a malformed
        identifier cannot crash an agent turn; the ownership check means no crafted
        string reaches another customer's data.
        """
        order = _scoped(customer_id).get_order(order_id=order_id)
        if order is not None:
            assert order.customer_id == customer_id

    @PROPERTY_SETTINGS
    @given(customer_id=st.text(max_size=100), order_id=st.text(max_size=100))
    def test_unknown_customers_see_nothing(self, customer_id: str, order_id: str) -> None:
        """An unrecognised customer id must never resolve to data.

        This cannot happen through the graph -- AUTHENTICATE resolves the id before the
        repository is constructed -- but the repository must not depend on its caller
        being careful. Defence in depth: the layer holds on its own.
        """
        repo = _scoped(customer_id)
        assert repo.get_order(order_id=order_id) is None
        assert repo.list_orders() == []

    @PROPERTY_SETTINGS
    @given(query=st.text(max_size=300))
    def test_search_never_raises_on_arbitrary_text(self, query: str) -> None:
        """Search input is untrusted: it comes straight from a support ticket.

        FTS5 MATCH has its own syntax, so unsanitised text raises `OperationalError`
        on a bare `AND` or an unbalanced quote. Raising would turn an ordinary ticket
        into a crashed agent turn, so the sanitiser must hold for *any* input --
        including the control characters and lone surrogates Hypothesis will try.
        """
        KnowledgeRepository(_readonly_db()).search(query)

    @PROPERTY_SETTINGS
    @given(query=st.text(max_size=300), limit=st.integers(min_value=1, max_value=10))
    def test_search_respects_any_limit(self, query: str, limit: int) -> None:
        hits = KnowledgeRepository(_readonly_db()).search(query, limit=limit)
        assert len(hits) <= limit


class TestOrderInvariantsHold:
    """The Order model's coherence, over every order and an arbitrary clock."""

    @PROPERTY_SETTINGS
    @given(
        order_id=_order_ids(),
        days_offset=st.integers(min_value=-400, max_value=400),
    )
    def test_lateness_stays_coherent_at_any_point_in_time(
        self, order_id: str, days_offset: int
    ) -> None:
        """Move the clock anywhere: the invariants still hold.

        Specifically, a terminal order is never late regardless of how far past its
        promised date the clock is moved. That is the bug found while inspecting the
        seeded data -- a delivered order reported as "20 days late" -- pinned as a
        property rather than as a single example.
        """
        clock = NOW + timedelta(days=days_offset)
        owner = (
            _readonly_db()
            .execute("SELECT customer_id FROM orders WHERE id = ?", (order_id,))
            .fetchone()["customer_id"]
        )
        order = _scoped(owner, now=clock).get_order(order_id=order_id)
        assert order is not None

        if order.status.is_terminal:
            assert order.is_late is False
            assert order.days_late is None
        elif order.is_late:
            assert order.days_late is not None
            assert order.days_late > 0
        else:
            assert order.days_late is None

    @PROPERTY_SETTINGS
    @given(order_id=_order_ids())
    def test_dispatch_fields_agree_with_status(self, order_id: str) -> None:
        """Shipping details travel together, and match the status.

        The schema enforces this on write; this checks the read path maps it faithfully
        -- a mapping bug could hand the agent a dispatched order with no tracking to
        offer, which reads as a data problem rather than a code one.
        """
        owner = (
            _readonly_db()
            .execute("SELECT customer_id FROM orders WHERE id = ?", (order_id,))
            .fetchone()["customer_id"]
        )
        order = _scoped(owner).get_order(order_id=order_id)
        assert order is not None

        has_shipping = order.shipped_at is not None
        assert has_shipping == (order.carrier is not None)
        assert has_shipping == (order.tracking_number is not None)

        if order.status.has_dispatched:
            assert has_shipping, f"{order.id} is {order.status} but never shipped"
        if order.status in {OrderStatus.PENDING, OrderStatus.PROCESSING, OrderStatus.CANCELLED}:
            assert not has_shipping, f"{order.id} is {order.status} but has shipping details"


class TestTicketAttribution:
    """The write path. Builds its own database per example, since it mutates."""

    @settings(deadline=None, max_examples=25)
    @given(customer_id=st.sampled_from(["CUST-0001", "CUST-0002", "CUST-0003", "CUST-0005"]))
    def test_a_created_ticket_always_belongs_to_the_bound_customer(self, customer_id: str) -> None:
        """The agent cannot open a ticket in someone else's name.

        There is no parameter for attribution -- it comes from the binding -- so this
        checks that the binding is what actually reaches the database, for any customer.

        Fewer examples than the read properties (25 rather than ~100) because each one
        builds a fresh database. The customer set is small and enumerated, so 25 covers
        it several times over.
        """
        db = connect()
        try:
            apply_schema(db)
            from env.seed import insert_anchors

            insert_anchors(db)

            repo = CustomerScopedRepository(db, customer_id=customer_id, now=NOW)
            ticket = repo.create_ticket(
                summary="A ticket created by a property-based test",
                priority=TicketPriority.LOW,
            )
            assert ticket.customer_id == customer_id

            stored = db.execute(
                "SELECT customer_id FROM tickets WHERE id = ?", (ticket.id,)
            ).fetchone()
            assert stored["customer_id"] == customer_id
        finally:
            db.close()


def test_the_property_tests_actually_cover_the_real_dataset() -> None:
    """Guards against the properties above passing vacuously.

    `sampled_from` over an empty list would make every property trivially true. This
    is the same trap the knowledge-search tests fell into while the FAQ table was
    empty: a test that cannot fail is a blind spot wearing the costume of coverage.
    """
    assert len(_all_customer_ids()) == 30
    assert len(_all_order_ids()) == 100
    assert datetime.now(UTC) is not None  # sanity: module imported and clock available
