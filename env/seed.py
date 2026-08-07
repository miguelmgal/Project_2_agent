"""Build the simulated database.

    uv run python -m env.seed

Two independent layers land in the same tables, and keeping them separate is the
whole design:

    Anchors  (env/anchors.py)  hand-written, fixed IDs   -> tests DO read these
    Filler   (this file)       Faker, fixed seed         -> tests NEVER read these

Faker does not look at the anchors and the anchors do not depend on Faker. The only
relationship is that they must not collide, which the ID ranges and the self-check
enforce.

**Why filler exists at all**, given no golden-set ticket references it:

1. *It contributes messy real-world strings.* Anchors are hand-written and
   well-behaved ("Maria Lopez"); the `es_ES` filler yields accents and eñes ("César",
   "Ramón"), hyphenated compounds ("Bellido-Donoso") and particles ("de Calatayud").
   Those exercise UTF-8 handling and, specifically, the FTS5 diacritics folding
   (`remove_diacritics 2`).

   Measured, not assumed: this locale produces **zero** apostrophes, so it does not
   cover quote-related SQL handling. The lesson generalises -- never rely on a locale
   to happen to emit hostile input. Genuinely adversarial strings (quotes, NULs,
   injection attempts) are deliberate test cases and belong in Hypothesis-driven
   tests via `st.text()`, not in seed data. Same philosophy as the anchors: what
   matters is written on purpose.
2. *It makes the customer filter demonstrable.* With only one customer in the
   database, a repository bug that forgets `WHERE customer_id = ?` still returns the
   "right" count, because that customer's orders are all the orders. With a hundred
   orders present, expected 3 versus actual 100 is impossible to miss.
3. *Realism* for the demo and the evaluation report, and the spec asks for Faker
   explicitly as a learning goal.

The agent itself never sees filler data: it can only read rows belonging to the
authenticated customer, and no anchor customer owns a filler order.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final

from faker import Faker

from env.anchors import (
    ALL_ORDER_STATUSES,
    ANCHOR_CUSTOMERS,
    ANCHOR_ORDERS,
    EXPECTED_ORDER_COUNTS,
    FILLER_CUSTOMER_PREFIX,
    FILLER_ORDER_PREFIX,
    REFERENCE_DATE,
)
from supportops.config import get_settings
from supportops.db.connection import allowed_check_values, connect
from supportops.db.connection import apply_schema as create_schema

if TYPE_CHECKING:
    import sqlite3

# --- Filler volume. Matches the spec's "~30 customers and ~100 orders". -------
# The exact numbers are arbitrary; the requirement is that filler is non-zero, so
# the three jobs above are fulfilled. A smaller dataset would work equally well.
FILLER_CUSTOMERS: Final = 26
FILLER_ORDERS: Final = 85

KNOWLEDGE_BASE_DIR: Final = Path(__file__).resolve().parent / "knowledge_base"
MIN_EXPECTED_ARTICLES: Final = 15
"""Per the spec. The seeder reports a shortfall rather than failing, because the
articles are Phase 1 piece 5 and the database must be buildable before then."""

UNDISPATCHED: Final = frozenset({"pending", "processing", "cancelled"})
DISPATCHED: Final = frozenset({"shipped", "delivered", "returned"})
CARRIERS: Final = ("DHL", "FedEx", "UPS", "Correos")

TERMINAL: Final = frozenset({"delivered", "cancelled", "returned"})
"""Statuses where the order's journey has ended.

**Lateness does not apply to these**, and that is a requirement for the tools, not
just for this file's reporting. A delivered order is not "20 days late" -- it was
delivered. An agent told that would produce an absurd answer from correct data,
which is the hardest kind of bug to attribute. `get_order_status` must therefore
report lateness only for non-terminal statuses.
"""

STATUS_WEIGHTS: Final[dict[str, int]] = {
    "delivered": 40,
    "shipped": 20,
    "processing": 12,
    "pending": 12,
    "delayed": 8,
    "cancelled": 5,
    "returned": 3,
}
"""Realistic-ish mix for filler orders.

A uniform draw makes 'returned' as common as 'delivered', which visibly contradicts
one of the three reasons filler exists (realism for the demo and the report). Weights
cost five lines and nothing reads the filler anyway, so there is no downside.
Coverage of every status still comes from the anchors, never from this distribution.
"""


# ------------------------------------------------------------------------ helpers


def _iso(moment: datetime) -> str:
    """Format as the schema's ISO-8601 UTC shape (validated by GLOB there)."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _reference_moment() -> datetime:
    """The frozen "now". Never `datetime.now()` -- see env/anchors.py."""
    return datetime.strptime(REFERENCE_DATE, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


# ------------------------------------------------------------------------- anchors


def insert_anchors(db: sqlite3.Connection) -> None:
    """Insert the hand-written rows the golden set references by name."""
    db.executemany(
        "INSERT INTO customers (id, email, full_name, tier, created_at) VALUES (?, ?, ?, ?, ?)",
        [(c.id, c.email, c.full_name, c.tier, c.created_at) for c in ANCHOR_CUSTOMERS],
    )
    db.executemany(
        "INSERT INTO orders (id, customer_id, status, total_amount_cents, currency, "
        "created_at, estimated_delivery, shipped_at, carrier, tracking_number) "
        "VALUES (?, ?, ?, ?, 'USD', ?, ?, ?, ?, ?)",
        [
            (
                o.id,
                o.customer_id,
                o.status,
                o.total_amount_cents,
                o.created_at,
                o.estimated_delivery,
                o.shipped_at,
                o.carrier,
                o.tracking_number,
            )
            for o in ANCHOR_ORDERS
        ],
    )
    db.commit()


# -------------------------------------------------------------------------- filler


def insert_filler(db: sqlite3.Connection, fake: Faker, seed: int) -> None:
    """Generate background volume with Faker.

    Deterministic: seeding Faker means the same input produces byte-identical output,
    which is what makes metrics comparable across runs.
    """
    Faker.seed(seed)

    # Emails must be unique (schema) and must not clash with an anchor. Faker can
    # repeat itself, so uniqueness is enforced here rather than hoped for.
    taken_emails = {c.email for c in ANCHOR_CUSTOMERS}
    customer_ids: list[str] = []

    for index in range(FILLER_CUSTOMERS):
        email = fake.unique.email().lower()
        while email in taken_emails:
            email = fake.unique.email().lower()
        taken_emails.add(email)

        customer_id = f"{FILLER_CUSTOMER_PREFIX}{index + 1:03d}"
        customer_ids.append(customer_id)
        db.execute(
            "INSERT INTO customers (id, email, full_name, tier, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                customer_id,
                email,
                fake.name(),
                "premium" if fake.random.random() < 0.2 else "standard",
                _iso(_reference_moment() - timedelta(days=fake.random.randint(30, 900))),
            ),
        )

    statuses = sorted(STATUS_WEIGHTS)
    weights = [STATUS_WEIGHTS[s] for s in statuses]
    for index in range(FILLER_ORDERS):
        # Filler orders belong ONLY to filler customers. If one attached to an anchor
        # customer, that anchor's order count would shift whenever the filler
        # changed, and the ambiguity case (exactly three orders) would break in
        # silence. The self-check asserts this holds.
        owner = fake.random.choice(customer_ids)
        status = fake.random.choices(statuses, weights=weights, k=1)[0]
        _insert_filler_order(db, fake, f"{FILLER_ORDER_PREFIX}{index + 1:03d}", owner, status)

    db.commit()


def _insert_filler_order(
    db: sqlite3.Connection, fake: Faker, order_id: str, owner: str, status: str
) -> None:
    """Insert one filler order that satisfies every schema invariant.

    The dispatch invariants are not optional: an order carrying shipping details in a
    pre-dispatch status is rejected by the database, so the generator has to respect
    the same business rules the agent will later rely on.
    """
    created = _reference_moment() - timedelta(days=fake.random.randint(1, 120))
    estimated = created + timedelta(days=fake.random.randint(3, 20))

    dispatched = status in DISPATCHED or (status == "delayed" and fake.random.random() < 0.5)
    if status in UNDISPATCHED:
        dispatched = False

    if dispatched:
        shipped: str | None = _iso(created + timedelta(days=fake.random.randint(1, 4)))
        carrier: str | None = fake.random.choice(CARRIERS)
        tracking: str | None = fake.bothify("??########", letters="ABCDEFGHJKLMNPQRSTUVWXYZ")
    else:
        shipped = carrier = tracking = None

    db.execute(
        "INSERT INTO orders (id, customer_id, status, total_amount_cents, currency, "
        "created_at, estimated_delivery, shipped_at, carrier, tracking_number) "
        "VALUES (?, ?, ?, ?, 'USD', ?, ?, ?, ?, ?)",
        (
            order_id,
            owner,
            status,
            fake.random.randint(500, 50_000),
            _iso(created),
            _iso(estimated),
            shipped,
            carrier,
            tracking,
        ),
    )


# ------------------------------------------------------------------ knowledge base


def load_knowledge_base(db: sqlite3.Connection) -> int:
    """Load FAQ articles from `env/knowledge_base/<category>/<slug>.md`.

    Layout carries the metadata, so there is nothing to parse: the parent directory
    is the category, the filename stem is the slug, and the first `# ` heading is the
    title. Markdown files are the source of truth -- version-controlled and
    reviewable in a PR -- and this table is the searchable projection of them.
    """
    if not KNOWLEDGE_BASE_DIR.exists():
        return 0

    loaded = 0
    for path in sorted(KNOWLEDGE_BASE_DIR.glob("*/*.md")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        title = next(
            (line.removeprefix("# ").strip() for line in lines if line.startswith("# ")),
            path.stem.replace("-", " ").capitalize(),
        )
        body = "\n".join(line for line in lines if not line.startswith("# ")).strip()

        loaded += 1
        db.execute(
            "INSERT INTO faq_articles (id, slug, title, body, category) VALUES (?, ?, ?, ?, ?)",
            (loaded, path.stem, title, body, path.parent.name),
        )
    db.commit()
    return loaded


# ---------------------------------------------------------------------- self-check


class SeedError(RuntimeError):
    """Raised when the generated database does not match its declared shape."""


def self_check(db: sqlite3.Connection) -> list[str]:
    """Verify the database matches what the golden set will assume.

    This runs at generation time on purpose. A badly generated database that reaches
    Phase 3 produces evaluation failures with no obvious cause -- the metric drops and
    the investigation looks at the agent, not at the data. Failing loudly here turns a
    mysterious four-hour debugging session into an immediate error message.
    """
    problems: list[str] = []

    # 1. Every anchor is present, with the status it was declared with.
    for customer in ANCHOR_CUSTOMERS:
        row = db.execute("SELECT tier FROM customers WHERE id = ?", (customer.id,)).fetchone()
        if row is None:
            problems.append(f"missing anchor customer {customer.id}")
        elif row["tier"] != customer.tier:
            problems.append(f"{customer.id}: tier is {row['tier']}, expected {customer.tier}")

    for order in ANCHOR_ORDERS:
        row = db.execute("SELECT status FROM orders WHERE id = ?", (order.id,)).fetchone()
        if row is None:
            problems.append(f"missing anchor order {order.id}")
        elif row["status"] != order.status:
            problems.append(f"{order.id}: status is {row['status']}, expected {order.status}")

    # 2. Declared order counts hold. CUST-0001 having exactly three orders is what
    #    makes the ambiguity case ambiguous; a fourth would silently defeat it.
    for customer_id, expected in EXPECTED_ORDER_COUNTS.items():
        actual = db.execute(
            "SELECT count(*) AS n FROM orders WHERE customer_id = ?", (customer_id,)
        ).fetchone()["n"]
        if actual != expected:
            problems.append(f"{customer_id}: has {actual} orders, expected exactly {expected}")

    # 3. No filler order belongs to an anchor customer. This is the invariant that
    #    keeps check 2 stable as the filler changes.
    leaked = db.execute(
        "SELECT count(*) AS n FROM orders WHERE id LIKE ? AND customer_id NOT LIKE ?",
        (f"{FILLER_ORDER_PREFIX}%", f"{FILLER_CUSTOMER_PREFIX}%"),
    ).fetchone()["n"]
    if leaked:
        problems.append(f"{leaked} filler orders are attached to non-filler customers")

    # 4. Status coverage must come from ANCHORS, not from luck. Filler statuses are
    #    random, so relying on them would make coverage probabilistic.
    schema_statuses = allowed_check_values(db, "orders", "status")
    anchor_statuses = {o.status for o in ANCHOR_ORDERS}
    if uncovered := schema_statuses - anchor_statuses:
        problems.append(f"statuses with no anchor order: {sorted(uncovered)}")
    if drifted := schema_statuses ^ ALL_ORDER_STATUSES:
        problems.append(f"schema statuses and env/anchors.py disagree: {sorted(drifted)}")

    # 5. Tickets start empty: the table is the forensic record of what the agent did.
    ticket_count = db.execute("SELECT count(*) AS n FROM tickets").fetchone()["n"]
    if ticket_count:
        problems.append(f"tickets table is not empty ({ticket_count} rows)")

    return problems


# ------------------------------------------------------------------------- reports


def _summarise(db: sqlite3.Connection, articles: int) -> None:
    counts = {
        table: db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608
        for table in ("customers", "orders", "tickets", "faq_articles")
    }
    print("\n  Rows written")
    for table, count in counts.items():
        print(f"    {table:<16}{count:>5}")

    print("\n  Anchor orders (the golden set's contract)")
    rows = db.execute(
        "SELECT o.id, o.customer_id, o.status, o.estimated_delivery, o.tracking_number, c.tier "
        "FROM orders o JOIN customers c ON c.id = o.customer_id "
        "WHERE o.customer_id LIKE 'CUST-0%' ORDER BY o.id"
    ).fetchall()
    ref = _reference_moment()
    print(f"    {'order':<10} {'customer':<11} {'tier':<9} {'status':<11} {'timing':<12} tracking")
    for row in rows:
        due = datetime.strptime(row["estimated_delivery"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        days = (ref - due).days
        # Lateness is meaningless once the journey has ended -- see TERMINAL.
        if row["status"] in TERMINAL:
            timing = "n/a (final)"
        elif days > 0:
            timing = f"{days}d late"
        else:
            timing = f"due in {-days}d"
        print(
            f"    {row['id']:<10} {row['customer_id']:<11} {row['tier']:<9} "
            f"{row['status']:<11} {timing:<12} {row['tracking_number'] or '-'}"
        )

    if articles < MIN_EXPECTED_ARTICLES:
        print(
            f"\n  PENDING: {articles} FAQ articles loaded, spec asks for "
            f"{MIN_EXPECTED_ARTICLES}+ (Phase 1 piece 5)."
        )


# ---------------------------------------------------------------------------- main


def build(db_path: Path | str, seed: int) -> int:
    """Create the database from scratch and verify it. Returns a process exit code."""
    print(f"  Building {db_path}  (Faker seed={seed}, frozen clock={REFERENCE_DATE})")

    db = connect(db_path)
    try:
        create_schema(db)
        insert_anchors(db)
        insert_filler(db, Faker("es_ES"), seed)
        articles = load_knowledge_base(db)

        problems = self_check(db)
        _summarise(db, articles)

        if problems:
            print("\n  SELF-CHECK FAILED")
            for problem in problems:
                print(f"    - {problem}")
            return 1
        print("\n  Self-check passed.")
        return 0
    finally:
        db.close()


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Build the simulated database")
    parser.add_argument("--db", default=str(settings.db_path), help="output database path")
    parser.add_argument("--seed", type=int, default=settings.faker_seed, help="Faker seed")
    args = parser.parse_args()
    return build(args.db, args.seed)


if __name__ == "__main__":
    sys.exit(main())
