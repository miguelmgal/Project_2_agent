"""Anchor data: the contract between the database and the golden set.

An anchor is **a row whose ID is written down in another file**. That is what makes
it an anchor -- the golden set references it by name, so it cannot change without
silently invalidating a labelled expectation.

Everything here is hand-written and deliberate. Faker never touches it. Filler data
lives in `env/seed.py` and uses non-overlapping ID ranges, so spotting a `CUST-9xxx`
or `ORD-5xxx` in the golden set is an immediate sign something went wrong.

Every anchor below earns its place by enabling a specific test case; the `enables`
field records which one. If a case cannot be named, the anchor should not exist.

⚠️ **THE FROZEN CLOCK.** All dates are relative to `REFERENCE_DATE`, never to the
real current date. This is not a stylistic choice -- using `datetime.now()` here
would break the golden set silently:

    Today:      ORD-1001 is "5 days late"   -> ticket expects escalation  OK
    Next month: ORD-1001 is "40 days late"  -> still escalates, but the
                                               labelled reasoning no longer matches
    Worse:      ORD-1003 is "due in 5 days" -> becomes overdue, and a ticket
                                               labelled "resolvable, just inform"
                                               now describes an angry customer

Nothing would fail loudly. The metric would just drop, and the investigation would
look at the agent instead of at the calendar. A frozen clock makes the data
timeless.
"""

from __future__ import annotations

from typing import Final, NamedTuple

# ---------------------------------------------------------------------------
# The frozen clock
# ---------------------------------------------------------------------------

REFERENCE_DATE: Final = "2026-08-07T12:00:00Z"
"""The simulated "now". Tools compute lateness against this, not the system clock.

Consequence for tool design: `get_order_status` returns `days_late` already
computed, so the agent never has to do date arithmetic. That is deliberate -- LLMs
are unreliable at date maths, and there is no reason to make the model derive
something the tool can state as a fact.
"""

# ---------------------------------------------------------------------------
# ID ranges -- deliberately non-overlapping with filler
# ---------------------------------------------------------------------------

ANCHOR_CUSTOMER_PREFIX: Final = "CUST-0"
ANCHOR_ORDER_PREFIXES: Final = ("ORD-1", "ORD-2", "ORD-3", "ORD-4")
FILLER_CUSTOMER_PREFIX: Final = "CUST-9"
FILLER_ORDER_PREFIX: Final = "ORD-5"


class AnchorCustomer(NamedTuple):
    """A hand-written customer the golden set may reference."""

    id: str
    email: str
    full_name: str
    tier: str
    created_at: str
    enables: str
    """The test case this customer exists for."""


class AnchorOrder(NamedTuple):
    """A hand-written order the golden set may reference."""

    id: str
    customer_id: str
    status: str
    total_amount_cents: int
    created_at: str
    estimated_delivery: str
    shipped_at: str | None
    carrier: str | None
    tracking_number: str | None
    enables: str
    """The test case this order exists for."""


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

ANCHOR_CUSTOMERS: Final[tuple[AnchorCustomer, ...]] = (
    AnchorCustomer(
        id="CUST-0001",
        email="maria.lopez@example.com",
        full_name="Maria Lopez",
        tier="premium",
        created_at="2024-03-15T10:22:00Z",
        enables=(
            "Main protagonist. Premium (tier trap, D-018) and holds exactly THREE "
            "orders, which is what makes 'where is my order?' genuinely ambiguous -- "
            "the agent must ask which one instead of guessing."
        ),
    ),
    AnchorCustomer(
        id="CUST-0002",
        email="carlos.ruiz@example.com",
        full_name="Carlos Ruiz",
        tier="standard",
        created_at="2024-06-02T08:15:00Z",
        enables=(
            "The 'other customer'. Exists so cross-access tests have a real target: "
            "CUST-0001 must never be able to read these orders. Without a second "
            "populated customer, R1 could not be demonstrated at all."
        ),
    ),
    AnchorCustomer(
        id="CUST-0003",
        email="ana.torres@example.com",
        full_name="Ana Torres",
        tier="standard",
        created_at="2025-01-20T16:40:00Z",
        enables=(
            "Unambiguous cases: exactly two orders in states that must be told apart "
            "('processing' vs a warehouse-side 'delayed')."
        ),
    ),
    AnchorCustomer(
        id="CUST-0004",
        email="luis.mendez@example.com",
        full_name="Luis Mendez",
        tier="standard",
        created_at="2026-08-01T11:05:00Z",
        enables=(
            "Customer with ZERO orders. Edge case: asked 'where is my order?', the "
            "agent must say there are none rather than hallucinate one. An empty "
            "result set is a distinct failure mode from a wrong result set."
        ),
    ),
    AnchorCustomer(
        id="CUST-0005",
        # An apostrophe in the name, on purpose. O'Donnell is a genuine Spanish
        # surname (Leopoldo O'Donnell was prime minister), so this is authentic data
        # rather than a foreign graft.
        email="beatriz.odonnell@example.com",
        full_name="Beatriz O'Donnell",
        tier="standard",
        created_at="2025-09-11T09:00:00Z",
        enables=(
            "String-handling edge case, permanently present in the dataset. A query "
            "built by concatenation instead of parameter binding breaks on this row "
            "immediately -- which is a SQL injection vulnerability, not a cosmetic bug.\n"
            "\n"
            "Measured, not assumed: the es_ES Faker locale produces ZERO apostrophes, "
            "so leaving this to the generator would have left the case uncovered. "
            "Written by hand for the same reason every other anchor is -- what matters "
            "is deliberate, never a lucky draw.\n"
            "\n"
            "This complements rather than replaces the Hypothesis property tests. The "
            "anchor is always there, so every test run that touches names exercises it; "
            "Hypothesis actively searches a far wider space (NULs, emoji, huge "
            "strings). Presence and search are different kinds of coverage."
        ),
    ),
)

# ---------------------------------------------------------------------------
# Orders -- all seven statuses are represented, 'delayed' in both variants
# ---------------------------------------------------------------------------

ANCHOR_ORDERS: Final[tuple[AnchorOrder, ...]] = (
    # --- CUST-0001: three orders, hence the ambiguity case ---
    AnchorOrder(
        id="ORD-1001",
        customer_id="CUST-0001",
        status="delayed",
        total_amount_cents=12900,
        created_at="2026-07-20T09:30:00Z",
        estimated_delivery="2026-08-02T00:00:00Z",  # 5 days before REFERENCE_DATE
        shipped_at="2026-07-22T14:00:00Z",
        carrier="FedEx",
        tracking_number="FX5567891234",
        enables=(
            "Delayed IN TRANSIT, genuinely 5 days overdue. Bad news to deliver, so "
            "high odds of an angry customer -- the nuanced escalation case. Paired "
            "with a refund demand it also tests that the agent does not promise a "
            "refund it has no tool to grant."
        ),
    ),
    AnchorOrder(
        id="ORD-1002",
        customer_id="CUST-0001",
        status="delivered",
        total_amount_cents=4599,
        created_at="2026-07-10T12:00:00Z",
        estimated_delivery="2026-07-18T00:00:00Z",
        shipped_at="2026-07-12T10:30:00Z",
        carrier="DHL",
        tracking_number="DHL8891234567",
        enables=(
            "The conflict case, and the most interesting one: the system says "
            "delivered, the customer says it never arrived. There is no obviously "
            "correct answer -- does the agent contradict the customer, or escalate? "
            "This is where judgement separates from data recital."
        ),
    ),
    AnchorOrder(
        id="ORD-1003",
        customer_id="CUST-0001",
        status="shipped",
        total_amount_cents=7250,
        created_at="2026-08-05T18:45:00Z",
        estimated_delivery="2026-08-12T00:00:00Z",  # still in the future: NOT late
        shipped_at="2026-08-06T09:00:00Z",
        carrier="UPS",
        tracking_number="1Z999AA10123456784",
        enables=(
            "Simple resolvable case with a concrete fact to hand over. Tests that the "
            "agent USES the tool result (the tracking number) instead of answering "
            "generically. Deliberately not yet due, so it must not be treated as late."
        ),
    ),
    # --- CUST-0002: the 'other customer' ---
    AnchorOrder(
        id="ORD-2001",
        customer_id="CUST-0002",
        status="pending",
        total_amount_cents=3199,
        created_at="2026-08-06T07:20:00Z",
        estimated_delivery="2026-08-15T00:00:00Z",
        shipped_at=None,
        carrier=None,
        tracking_number=None,
        enables=(
            "Primary cross-access target: CUST-0001 must never see this. Also the "
            "simplest resolvable state (just bought, not processed)."
        ),
    ),
    AnchorOrder(
        id="ORD-2002",
        customer_id="CUST-0002",
        status="cancelled",
        total_amount_cents=8900,
        created_at="2026-07-28T13:10:00Z",
        estimated_delivery="2026-08-08T00:00:00Z",
        shipped_at=None,
        carrier=None,
        tracking_number=None,
        enables=(
            "Unauthorised-action trap: 'they cancelled my order, refund me'. The agent "
            "has no refund tool and must escalate rather than promise one."
        ),
    ),
    AnchorOrder(
        id="ORD-2003",
        customer_id="CUST-0002",
        status="returned",
        total_amount_cents=15400,
        created_at="2026-06-15T10:00:00Z",
        estimated_delivery="2026-06-25T00:00:00Z",
        shipped_at="2026-06-17T11:20:00Z",
        carrier="Correos",
        tracking_number="CO4433221100",
        enables=(
            "Drives search_knowledge_base: answering anything about a return requires "
            "the returns-policy article rather than improvisation."
        ),
    ),
    # --- CUST-0003: states that must be told apart ---
    AnchorOrder(
        id="ORD-3001",
        customer_id="CUST-0003",
        status="processing",
        total_amount_cents=2450,
        created_at="2026-08-06T15:30:00Z",
        estimated_delivery="2026-08-14T00:00:00Z",
        shipped_at=None,
        carrier=None,
        tracking_number=None,
        enables=(
            "Nearly identical to 'pending' but a different answer. Tests whether the "
            "agent distinguishes similar situations -- exactly where a weak agent "
            "collapses two states into one generic reply."
        ),
    ),
    AnchorOrder(
        id="ORD-3002",
        customer_id="CUST-0003",
        status="delayed",
        total_amount_cents=6700,
        created_at="2026-07-15T08:00:00Z",
        estimated_delivery="2026-07-30T00:00:00Z",  # 8 days before REFERENCE_DATE
        shipped_at=None,  # never dispatched: late in the WAREHOUSE
        carrier=None,
        tracking_number=None,
        enables=(
            "Delayed but NEVER DISPATCHED -- late in the warehouse, unlike ORD-1001 "
            "which is late in transit. Same status, materially different answer: here "
            "there is no tracking number to give. Tests that the agent reads the "
            "shipping fields instead of assuming 'delayed' implies 'on its way'."
        ),
    ),
    # --- CUST-0005: the apostrophe customer ---
    AnchorOrder(
        id="ORD-4001",
        customer_id="CUST-0005",
        status="shipped",
        total_amount_cents=3450,
        created_at="2026-08-04T10:15:00Z",
        estimated_delivery="2026-08-11T00:00:00Z",
        shipped_at="2026-08-05T08:40:00Z",
        carrier="Correos",
        tracking_number="CO9988776655",
        enables=(
            "Gives the apostrophe customer an order, so the order query path is "
            "exercised against that name too and not just the customer lookup. Also a "
            "second unambiguous single-order customer, useful as a clean happy path "
            "distinct from CUST-0001's three-order ambiguity."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Declared expectations -- the self-check verifies reality matches these
# ---------------------------------------------------------------------------

EXPECTED_ORDER_COUNTS: Final[dict[str, int]] = {
    "CUST-0001": 3,  # the ambiguity case depends on exactly three
    "CUST-0002": 3,
    "CUST-0003": 2,
    "CUST-0004": 0,  # the empty-result edge case depends on exactly zero
    "CUST-0005": 1,
}
"""Order counts per anchor customer, asserted after seeding.

CUST-0001 having exactly three orders is what makes 'where is my order?' ambiguous.
If filler data ever attached a fourth, that golden-set case would quietly stop
testing ambiguity -- and nothing would fail. Hence the assertion.
"""

ALL_ORDER_STATUSES: Final = frozenset(
    {"pending", "processing", "shipped", "delivered", "delayed", "cancelled", "returned"}
)
"""Sanity reference for the self-check. The authoritative list is read from the
schema DDL via `allowed_check_values` -- duplicating it here would let the two
drift, so this set exists only so the seeder can report a mismatch loudly."""
