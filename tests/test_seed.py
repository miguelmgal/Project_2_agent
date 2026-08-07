"""Data generation tests.

Deterministic, no LLM, runs on every PR (CLAUDE.md section 7, "unit" level).

The property under test is the one the entire project rests on: **the same seed
produces the same database**. Without it, metrics from two runs are not comparable
and every measurement in Phase 3 becomes anecdote.

The most important test here is `test_self_check_catches_a_broken_database`. A
self-check that never fails is decoration -- it has to be shown rejecting something.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from env.anchors import (
    ANCHOR_CUSTOMERS,
    ANCHOR_ORDERS,
    EXPECTED_ORDER_COUNTS,
    FILLER_CUSTOMER_PREFIX,
    FILLER_ORDER_PREFIX,
    REFERENCE_DATE,
)
from env.seed import build, self_check

from supportops.db import connect
from supportops.db.connection import allowed_check_values

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

DEFAULT_SEED = 42
OTHER_SEED = 99


def _fingerprint(db_path: Path) -> str:
    """Hash the data, not the file.

    SQLite files contain page-layout bytes that can differ between identical
    databases, so comparing files would produce false negatives. Hashing rows in a
    deterministic order compares what actually matters.
    """
    db = connect(db_path)
    digest = hashlib.sha256()
    try:
        for table in ("customers", "orders", "tickets", "faq_articles"):
            for row in db.execute(f"SELECT * FROM {table} ORDER BY 1"):  # noqa: S608
                digest.update(repr(tuple(row)).encode())
    finally:
        db.close()
    return digest.hexdigest()


def _anchor_rows(db_path: Path) -> list[tuple[object, ...]]:
    db = connect(db_path)
    try:
        rows = db.execute(
            "SELECT id, customer_id, status, total_amount_cents, created_at, "
            "estimated_delivery, shipped_at, carrier, tracking_number "
            "FROM orders WHERE id NOT LIKE ? ORDER BY id",
            (f"{FILLER_ORDER_PREFIX}%",),
        ).fetchall()
        return [tuple(row) for row in rows]
    finally:
        db.close()


@pytest.fixture
def built_db(tmp_path: Path) -> Path:
    """A freshly built database at the default seed."""
    path = tmp_path / "seeded.db"
    assert build(path, seed=DEFAULT_SEED) == 0
    return path


# ---------------------------------------------------------------------- determinism


class TestDeterminism:
    def test_same_seed_produces_identical_data(self, tmp_path: Path) -> None:
        """The property the whole project rests on."""
        first, second = tmp_path / "a.db", tmp_path / "b.db"
        build(first, seed=DEFAULT_SEED)
        build(second, seed=DEFAULT_SEED)
        assert _fingerprint(first) == _fingerprint(second)

    def test_different_seed_produces_different_data(self, tmp_path: Path) -> None:
        """Proves the seed is actually wired through.

        Without this, a bug that ignored the seed would leave the test above green --
        two identical databases pass an equality check whether or not the seed is
        honoured.
        """
        first, second = tmp_path / "a.db", tmp_path / "b.db"
        build(first, seed=DEFAULT_SEED)
        build(second, seed=OTHER_SEED)
        assert _fingerprint(first) != _fingerprint(second)

    def test_anchors_are_immune_to_the_seed(self, tmp_path: Path) -> None:
        """The core of the anchor design.

        Anchor rows are hand-written, so changing the Faker seed -- or adding filler
        customers -- must not touch them. This is what makes it safe to write a golden
        set ticket that says "ORD-1001 is delayed": the claim cannot rot underneath.
        """
        first, second = tmp_path / "a.db", tmp_path / "b.db"
        build(first, seed=DEFAULT_SEED)
        build(second, seed=OTHER_SEED)
        assert _anchor_rows(first) == _anchor_rows(second)


# --------------------------------------------------------------------- frozen clock


class TestFrozenClock:
    def test_lateness_is_measured_against_the_reference_date(self, built_db: Path) -> None:
        """ORD-1001 is exactly 5 days late, permanently.

        This is what catches a regression to `datetime.now()`. With a real clock the
        figure grows every day, and a golden-set ticket labelled around "5 days late"
        slowly stops describing the data -- without anything failing.
        """
        db = connect(built_db)
        try:
            due = db.execute(
                "SELECT estimated_delivery FROM orders WHERE id = 'ORD-1001'"
            ).fetchone()["estimated_delivery"]
        finally:
            db.close()

        reference = datetime.strptime(REFERENCE_DATE, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        deadline = datetime.strptime(due, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        assert (reference - deadline).days == 5

    def test_a_not_yet_due_order_stays_not_due(self, built_db: Path) -> None:
        """ORD-1003 must never drift into being overdue.

        It backs a ticket labelled "resolvable, just inform". If it became late, that
        ticket would silently start describing an angry customer instead.
        """
        db = connect(built_db)
        try:
            due = db.execute(
                "SELECT estimated_delivery FROM orders WHERE id = 'ORD-1003'"
            ).fetchone()["estimated_delivery"]
        finally:
            db.close()
        assert due > REFERENCE_DATE


# -------------------------------------------------------------- anchor/filler split


class TestAnchorFillerSeparation:
    def test_declared_order_counts_hold(self, built_db: Path) -> None:
        """CUST-0001 must own exactly three orders.

        That is what makes "where is my order?" ambiguous. A fourth order -- from
        filler leakage, say -- would quietly turn an ambiguity test into an
        unambiguous one.
        """
        db = connect(built_db)
        try:
            for customer_id, expected in EXPECTED_ORDER_COUNTS.items():
                actual = db.execute(
                    "SELECT count(*) AS n FROM orders WHERE customer_id = ?", (customer_id,)
                ).fetchone()["n"]
                assert actual == expected, f"{customer_id} has {actual}, expected {expected}"
        finally:
            db.close()

    def test_no_filler_order_belongs_to_an_anchor_customer(self, built_db: Path) -> None:
        """The invariant that keeps the counts above stable as filler changes."""
        db = connect(built_db)
        try:
            leaked = db.execute(
                "SELECT count(*) AS n FROM orders WHERE id LIKE ? AND customer_id NOT LIKE ?",
                (f"{FILLER_ORDER_PREFIX}%", f"{FILLER_CUSTOMER_PREFIX}%"),
            ).fetchone()["n"]
            assert leaked == 0
        finally:
            db.close()

    def test_anchor_and_filler_id_ranges_do_not_overlap(self, built_db: Path) -> None:
        """Seeing an ORD-5xxx in the golden set must be an obvious mistake."""
        anchor_ids = {o.id for o in ANCHOR_ORDERS} | {c.id for c in ANCHOR_CUSTOMERS}
        assert not any(
            i.startswith((FILLER_ORDER_PREFIX, FILLER_CUSTOMER_PREFIX)) for i in anchor_ids
        )

    def test_every_status_is_covered_by_an_anchor(self, built_db: Path) -> None:
        """Coverage must be deliberate, never a lucky draw.

        Filler statuses are random, so leaning on them would make domain coverage
        probabilistic -- a status could vanish from the dataset on a seed change.
        """
        db = connect(built_db)
        try:
            schema_statuses = allowed_check_values(db, "orders", "status")
        finally:
            db.close()
        assert schema_statuses <= {o.status for o in ANCHOR_ORDERS}

    def test_delayed_exists_both_dispatched_and_not(self, built_db: Path) -> None:
        """'delayed' is the one ambiguous status, so both shapes must be present.

        Late in transit (has tracking) and late in the warehouse (has none) demand
        materially different answers from the agent.
        """
        delayed = [o for o in ANCHOR_ORDERS if o.status == "delayed"]
        assert any(o.shipped_at is not None for o in delayed)
        assert any(o.shipped_at is None for o in delayed)


# ----------------------------------------------------------------------- self-check


class TestSelfCheck:
    def test_passes_on_a_freshly_built_database(self, built_db: Path) -> None:
        db = connect(built_db)
        try:
            assert self_check(db) == []
        finally:
            db.close()

    @pytest.mark.parametrize(
        ("label", "damage"),
        [
            ("missing anchor order", "DELETE FROM orders WHERE id = 'ORD-1001'"),
            (
                "wrong anchor status",
                "UPDATE orders SET status='pending', shipped_at=NULL, "
                "carrier=NULL, tracking_number=NULL WHERE id='ORD-1003'",
            ),
            (
                "wrong tier on the trap customer",
                "UPDATE customers SET tier='standard' WHERE id='CUST-0001'",
            ),
            (
                "pre-existing ticket",
                "INSERT INTO tickets (id, customer_id, summary, priority, "
                "status, escalated, created_at) VALUES ('TCK-X','CUST-0001',"
                "'Leftover row that should not exist','low','open',0,"
                "'2026-08-07T10:00:00Z')",
            ),
        ],
    )
    def test_self_check_catches_a_broken_database(
        self, built_db: Path, label: str, damage: str
    ) -> None:
        """The most important test in this file.

        A self-check that has never been observed failing is decoration. Each case here
        breaks one assumption the golden set depends on and asserts the check notices.
        """
        db = connect(built_db)
        try:
            db.execute(damage)
            db.commit()
            problems = self_check(db)
            assert problems, f"self-check did not notice: {label}"
        finally:
            db.close()

    def test_extra_order_on_an_anchor_customer_is_caught(self, built_db: Path) -> None:
        """The silent failure the count assertion exists for.

        A fourth order on CUST-0001 breaks nothing structurally -- the database accepts
        it happily -- while destroying the ambiguity case. Only the count check sees it.
        """
        db: sqlite3.Connection = connect(built_db)
        try:
            db.execute(
                "INSERT INTO orders (id, customer_id, status, total_amount_cents, currency, "
                "created_at, estimated_delivery, shipped_at, carrier, tracking_number) "
                "VALUES ('ORD-1099','CUST-0001','pending',1000,'USD','2026-08-01T00:00:00Z',"
                "'2026-08-20T00:00:00Z',NULL,NULL,NULL)"
            )
            db.commit()
            problems = self_check(db)
            assert any("CUST-0001" in p for p in problems)
        finally:
            db.close()
