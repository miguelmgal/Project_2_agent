"""Data access. The only module in the project that speaks SQL.

Three classes, split by **privilege** rather than by table. That split is the security
design (CLAUDE.md section 3, R1):

    AuthRepository            resolves identity      -> AUTHENTICATE node only
    CustomerScopedRepository  bound to ONE customer  -> the tools
    KnowledgeRepository       public data            -> the tools

The object the tools receive does not contain the method that resolves identity. Not
"must not be used" -- it is not there.

**Why the scoped repository is bound at construction rather than taking a
`customer_id` argument.** A method signature like

    get_order(order_id: str, customer_id: str)

still allows passing the wrong value: both parameters are `str`, so swapping them
type-checks, runs, returns None, and looks exactly like "not found". Binding the
customer at construction removes the parameter, and a parameter that does not exist
cannot be wrong. It is the same closure technique the tools factory uses, applied one
layer deeper -- defence in depth, so a carelessly written tool still cannot reach
another customer's data.

**Industry context.** The canonical mechanism for multi-tenant isolation is database
enforced: Postgres Row-Level Security applies the tenant filter to *every* query,
including one someone writes badly tomorrow. SQLite has no RLS, so the guarantee lives
in this module, which is the closest equivalent available. If this moved to production
on Postgres, isolation would move to RLS and this module would become the second
barrier rather than the only one (D-025).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from supportops.db.models import (
    Customer,
    Escalation,
    FaqHit,
    Order,
    OrderStatus,
    Ticket,
    TicketPriority,
    TicketStatus,
    parse_timestamp,
)

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime

_ORDER_COLUMNS: Final = (
    "id, customer_id, status, total_amount_cents, currency, created_at, "
    "estimated_delivery, shipped_at, carrier, tracking_number"
)


# ---------------------------------------------------------------------------
# Identity resolution -- privileged, never handed to a tool
# ---------------------------------------------------------------------------


class AuthRepository:
    """Resolves an email address to a customer.

    The only operation that *establishes* identity, so it cannot receive one. That
    also makes it the most sensitive method in the project: exposed as a tool, it
    would be a customer enumerator -- feed it addresses and see which ones exist.

    It is therefore never exposed as a tool and lives in its own class, so the object
    the tools hold has no way to reach it.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def resolve_by_email(self, email: str) -> Customer | None:
        """Find the customer who owns this email address.

        Returns None if nobody does. The AUTHENTICATE node escalates on None rather
        than guessing -- an unrecognised address is a case for a human, not for
        improvisation.
        """
        row = self._db.execute(
            "SELECT id, email, full_name, tier FROM customers WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
        return None if row is None else Customer(**dict(row))


# ---------------------------------------------------------------------------
# Customer-scoped access -- what the tools get
# ---------------------------------------------------------------------------


class CustomerScopedRepository:
    """Data access bound to a single, already-authenticated customer.

    Every query in this class filters on `self._customer_id`, which is set once at
    construction and never afterwards. There is no method that reads customer data
    without it, and no parameter through which a caller could supply a different one.

    A reviewer can establish that cross-customer access is impossible by reading the
    constructor and confirming no method takes a customer identifier -- without
    auditing a single SQL string.
    """

    def __init__(self, connection: sqlite3.Connection, *, customer_id: str, now: datetime) -> None:
        """Bind to one customer.

        Args:
            connection: an open connection (foreign keys already enforced).
            customer_id: the verified customer. Must come from `AuthRepository`,
                never from model output.
            now: the reference instant for lateness. Injected rather than read from
                the system clock so evaluation results stay reproducible (D-022);
                passing it also makes the dependency visible instead of hidden.
        """
        self._db = connection
        self._customer_id = customer_id
        self._now = now

    @property
    def customer_id(self) -> str:
        """The bound customer. Read-only: rebinding would defeat the whole design."""
        return self._customer_id

    # ------------------------------------------------------------------ reads

    def get_customer(self) -> Customer | None:
        """The bound customer's profile.

        No parameters, because there is only one customer this repository can see.
        """
        row = self._db.execute(
            "SELECT id, email, full_name, tier FROM customers WHERE id = ?",
            (self._customer_id,),
        ).fetchone()
        return None if row is None else Customer(**dict(row))

    def get_order(self, *, order_id: str) -> Order | None:
        """One order belonging to the bound customer.

        Returns None both when the order does not exist and when it belongs to someone
        else. **The two cases are deliberately indistinguishable.**

        Distinguishing them would leak information: try a thousand identifiers, and
        the ones answering "not yours" are the ones that exist. That is CWE-204,
        Observable Response Discrepancy -- the same reason a login says "username or
        password incorrect" rather than "that password is wrong".
        """
        row = self._db.execute(
            f"SELECT {_ORDER_COLUMNS} FROM orders WHERE id = ? AND customer_id = ?",  # noqa: S608
            (order_id, self._customer_id),
        ).fetchone()
        return None if row is None else self._to_order(row)

    def list_orders(self) -> list[Order]:
        """Every order belonging to the bound customer, newest first.

        An empty list is a valid answer, not an error: a customer with no orders is a
        real case (CUST-0004), and "you have no orders" is a different thing for the
        agent to say than "something went wrong".
        """
        rows = self._db.execute(
            f"SELECT {_ORDER_COLUMNS} FROM orders WHERE customer_id = ? "  # noqa: S608
            "ORDER BY created_at DESC",
            (self._customer_id,),
        ).fetchall()
        return [self._to_order(row) for row in rows]

    # ----------------------------------------------------------------- writes

    def create_ticket(
        self,
        *,
        summary: str,
        priority: TicketPriority,
        escalation: Escalation | None = None,
    ) -> Ticket:
        """Create a ticket for the bound customer. The only write in the project.

        `escalation` carries category and reason together because the schema requires
        both or neither. As one optional object the invalid state is unconstructible;
        as two optional parameters a caller could supply half and hit a constraint
        error at the database.
        """
        status = TicketStatus.ESCALATED if escalation else TicketStatus.OPEN
        ticket_id = self._next_ticket_id()
        created_at = self._now.strftime("%Y-%m-%dT%H:%M:%SZ")

        self._db.execute(
            "INSERT INTO tickets (id, customer_id, summary, priority, status, escalated, "
            "escalation_category, escalation_reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticket_id,
                self._customer_id,
                summary,
                priority.value,
                status.value,
                1 if escalation else 0,
                escalation.category.value if escalation else None,
                escalation.reason if escalation else None,
                created_at,
            ),
        )
        self._db.commit()

        return Ticket(
            id=ticket_id,
            customer_id=self._customer_id,
            summary=summary,
            priority=priority,
            status=status,
            escalation=escalation,
            created_at=parse_timestamp(created_at),
        )

    def list_tickets(self) -> list[Ticket]:
        """The bound customer's tickets. Used by evaluation, not by the agent.

        Reading back what the agent wrote is how escalation metrics are measured, so
        the accessor belongs here rather than in test code.
        """
        rows = self._db.execute(
            "SELECT id, customer_id, summary, priority, status, escalation_category, "
            "escalation_reason, created_at FROM tickets WHERE customer_id = ? "
            "ORDER BY created_at, id",
            (self._customer_id,),
        ).fetchall()
        return [self._to_ticket(row) for row in rows]

    # ---------------------------------------------------------------- mapping

    def _next_ticket_id(self) -> str:
        """Sequential, human-readable ticket identifiers.

        A counter rather than a UUID because these end up in evaluation reports and
        traces that a person reads: TCK-0003 is quotable, a UUID is not.
        """
        row = self._db.execute("SELECT count(*) AS n FROM tickets").fetchone()
        return f"TCK-{row['n'] + 1:04d}"

    def _to_order(self, row: sqlite3.Row) -> Order:
        """Map a row to an Order, resolving lateness against the injected clock."""
        status = OrderStatus(row["status"])
        due = parse_timestamp(row["estimated_delivery"])

        # Lateness applies only while the journey is unfinished. A delivered order is
        # not late -- it was delivered, and telling the agent otherwise yields an
        # absurd answer built on correct data.
        if status.is_terminal:
            days_late: int | None = None
            is_late = False
        else:
            overdue = (self._now - due).days
            is_late = overdue > 0
            days_late = overdue if is_late else None

        return Order(
            id=row["id"],
            customer_id=row["customer_id"],
            status=status,
            total_amount_cents=row["total_amount_cents"],
            currency=row["currency"],
            created_at=parse_timestamp(row["created_at"]),
            estimated_delivery=due,
            shipped_at=parse_timestamp(row["shipped_at"]) if row["shipped_at"] else None,
            carrier=row["carrier"],
            tracking_number=row["tracking_number"],
            days_late=days_late,
            is_late=is_late,
        )

    @staticmethod
    def _to_ticket(row: sqlite3.Row) -> Ticket:
        escalation = (
            Escalation(category=row["escalation_category"], reason=row["escalation_reason"])
            if row["escalation_category"]
            else None
        )
        return Ticket(
            id=row["id"],
            customer_id=row["customer_id"],
            summary=row["summary"],
            priority=TicketPriority(row["priority"]),
            status=TicketStatus(row["status"]),
            escalation=escalation,
            created_at=parse_timestamp(row["created_at"]),
        )


# ---------------------------------------------------------------------------
# Public knowledge -- no identity involved
# ---------------------------------------------------------------------------


class KnowledgeRepository:
    """Full-text search over the FAQ articles.

    Takes no customer identity, and that is correct rather than an oversight: articles
    belong to nobody, so there is nothing to scope. It follows directly from
    `faq_articles` having no foreign key to `customers`, and it is why
    `search_knowledge_base` is the one tool needing no injected identity.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def search(self, query: str, *, limit: int = 3) -> list[FaqHit]:
        """Rank articles by relevance to a free-text query.

        Uses FTS5's BM25 ranking. `LIKE '%query%'` would return nothing for "no puedo
        entrar en mi cuenta" against an article titled "Restablecer tu contrasena" --
        there is no shared substring. BM25 finds it by term relevance.

        Returns an empty list for a query FTS5 cannot parse, rather than raising. The
        query text originates from a support ticket, so it is untrusted input: bare
        operators like `AND` or an unbalanced quote are ordinary customer prose, not
        exceptional conditions.
        """
        cleaned = _to_fts_query(query)
        if not cleaned:
            return []

        rows = self._db.execute(
            "SELECT a.slug, a.title, a.body, a.category, bm25(faq_search) AS score "
            "FROM faq_search JOIN faq_articles a ON a.id = faq_search.rowid "
            "WHERE faq_search MATCH ? ORDER BY score LIMIT ?",
            (cleaned, limit),
        ).fetchall()

        # bm25() returns negative values, more negative meaning more relevant. Flipped
        # here so the agent sees "higher is better", which is what it will assume.
        return [
            FaqHit(
                slug=row["slug"],
                title=row["title"],
                body=row["body"],
                category=row["category"],
                relevance=-float(row["score"]),
            )
            for row in rows
        ]


# Spanish function words. Not linguistic completeness -- just the high-frequency words
# that appear in almost every article and therefore carry no discriminating signal.
#
# Why this exists: OR-ing every word in the query made "garantia del procesador" return
# the shipping article, because "del" occurs in "salen del almacen". The agent would
# then hold an irrelevant article with a plausible-looking score (0.53) and could
# answer from it instead of concluding that nothing relevant was found. Precision at
# the retrieval layer is what makes "I found nothing" a reachable state.
_STOPWORDS: Final = frozenset(
    {
        "a",
        "al",
        "algo",
        "como",
        "con",
        "cual",
        "cuando",
        "de",
        "del",
        "desde",
        "donde",
        "dos",
        "el",
        "ella",
        "ellos",
        "en",
        "entre",
        "era",
        "es",
        "esa",
        "ese",
        "esta",
        "este",
        "esto",
        "ha",
        "hay",
        "la",
        "las",
        "le",
        "les",
        "lo",
        "los",
        "mas",
        "me",
        "mi",
        "mis",
        "muy",
        "no",
        "nos",
        "o",
        "para",
        "pero",
        "por",
        "porque",
        "que",
        "quien",
        "se",
        "si",
        "sin",
        "sobre",
        "solo",
        "son",
        "su",
        "sus",
        "tan",
        "te",
        "tiene",
        "todo",
        "tu",
        "un",
        "una",
        "uno",
        "y",
        "ya",
    }
)

_MIN_TERM_LENGTH: Final = 3


def _to_fts_query(query: str) -> str:
    """Turn free-form customer prose into a safe, discriminating FTS5 MATCH expression.

    Two jobs, both necessary:

    1. **Make it inert.** FTS5 MATCH has its own syntax (`AND`, `OR`, `NOT`, `NEAR`,
       quotes, prefixes) and raw ticket text will contain characters that break it --
       verified: a bare `AND` raises `fts5: syntax error`, an unbalanced quote raises
       `unterminated string`. Quoting each term turns an injected operator into a
       literal search term.
    2. **Keep it discriminating.** Function words are dropped, because a term present
       in every article matches every article. See `_STOPWORDS`.

    Terms are OR-ed rather than AND-ed: a customer who writes six words about a
    problem should still match an article covering four of them. That is a deliberate
    recall-over-precision choice, and it is measurable -- Phase 3 evaluates retrieval
    against the golden set, so if precision turns out to be the binding constraint the
    trade-off can be revisited with data instead of intuition.

    Returns an empty string when nothing survives filtering (an empty query, only
    punctuation, or only function words), which the caller turns into no results.
    """
    normalised = "".join(char if char.isalnum() else " " for char in query.lower())
    terms = [
        word
        for word in normalised.split()
        if len(word) >= _MIN_TERM_LENGTH and word not in _STOPWORDS
    ]
    return " OR ".join(f'"{term}"' for term in terms)
