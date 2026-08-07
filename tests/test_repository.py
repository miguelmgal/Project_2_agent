"""Repository tests, example-based.

Deterministic, no LLM, runs on every PR (CLAUDE.md section 7, "unit" level).

These document **concrete business cases** in a form anyone can read: "CUST-0001
cannot see ORD-2001". The universal version of the same guarantee lives in
`test_repository_properties.py` -- both exist on purpose. Examples state intent;
properties prove there is no counterexample.

The security tests worth reading closely are in `TestNoExistenceLeak`. Returning the
same answer for "does not exist" and "belongs to someone else" is not tidiness -- the
difference would be an enumeration oracle (CWE-204).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from env.anchors import REFERENCE_DATE

from supportops.db import AuthRepository, CustomerScopedRepository, KnowledgeRepository
from supportops.db.models import (
    Escalation,
    EscalationCategory,
    OrderStatus,
    TicketPriority,
    TicketStatus,
    parse_timestamp,
)

if TYPE_CHECKING:
    import sqlite3

NOW = parse_timestamp(REFERENCE_DATE)

MARIA = "CUST-0001"
CARLOS = "CUST-0002"
NO_ORDERS = "CUST-0004"
APOSTROPHE = "CUST-0005"

MARIA_DELAYED = "ORD-1001"
MARIA_DELIVERED = "ORD-1002"
MARIA_SHIPPED = "ORD-1003"
CARLOS_PENDING = "ORD-2001"

# One content word from each mechanism article, so a query using all three matches
# all three. Needed to assert LIMIT by equality rather than by inequality.
ALL_THREE_ARTICLES = "contrasena transportista devoluciones"


@pytest.fixture
def scoped(seeded_full_db: sqlite3.Connection) -> CustomerScopedRepository:
    """A repository bound to Maria, the main protagonist."""
    return CustomerScopedRepository(seeded_full_db, customer_id=MARIA, now=NOW)


# ---------------------------------------------------------------- identity resolution


class TestAuthRepository:
    """Identity resolution. Privileged: never exposed as a tool."""

    def test_resolves_a_known_email(self, seeded_full_db: sqlite3.Connection) -> None:
        customer = AuthRepository(seeded_full_db).resolve_by_email("maria.lopez@example.com")
        assert customer is not None
        assert customer.id == MARIA

    def test_returns_none_for_an_unknown_email(self, seeded_full_db: sqlite3.Connection) -> None:
        """The AUTHENTICATE node escalates on None rather than guessing."""
        assert AuthRepository(seeded_full_db).resolve_by_email("nobody@example.com") is None

    @pytest.mark.parametrize(
        "variant",
        ["MARIA.LOPEZ@EXAMPLE.COM", "  maria.lopez@example.com  ", "Maria.Lopez@Example.com"],
    )
    def test_email_lookup_is_normalised(
        self, seeded_full_db: sqlite3.Connection, variant: str
    ) -> None:
        """Customers do not type their address consistently.

        Failing to normalise here would mean a legitimate customer gets escalated as
        unrecognised -- a false escalation caused by capital letters.
        """
        customer = AuthRepository(seeded_full_db).resolve_by_email(variant)
        assert customer is not None
        assert customer.id == MARIA

    def test_scoped_repository_cannot_resolve_identity(
        self, scoped: CustomerScopedRepository
    ) -> None:
        """The object the tools receive has no identity-resolution method.

        Not "must not be used" -- absent. A tool written carelessly tomorrow cannot
        enumerate customers because there is nothing to call.
        """
        assert not hasattr(scoped, "resolve_by_email")


# --------------------------------------------------------------------- customer scope


class TestCustomerScope:
    def test_reads_the_bound_customer(self, scoped: CustomerScopedRepository) -> None:
        customer = scoped.get_customer()
        assert customer is not None
        assert customer.id == MARIA
        assert customer.tier == "premium"  # the D-018 trap

    def test_reads_own_order(self, scoped: CustomerScopedRepository) -> None:
        order = scoped.get_order(order_id=MARIA_SHIPPED)
        assert order is not None
        assert order.tracking_number == "1Z999AA10123456784"

    def test_lists_exactly_the_three_own_orders(self, scoped: CustomerScopedRepository) -> None:
        """Three is what makes "where is my order?" ambiguous.

        The number is asserted, not just the ownership: if this returned 100, the
        customer filter would be missing entirely.
        """
        orders = scoped.list_orders()
        assert len(orders) == 3
        assert {o.id for o in orders} == {MARIA_DELAYED, MARIA_DELIVERED, MARIA_SHIPPED}

    def test_a_customer_with_no_orders_gets_an_empty_list(
        self, seeded_full_db: sqlite3.Connection
    ) -> None:
        """Empty is a valid answer, not an error.

        "You have no orders" is a different thing for the agent to say than "something
        went wrong", and it must not hallucinate an order to fill the gap.
        """
        repo = CustomerScopedRepository(seeded_full_db, customer_id=NO_ORDERS, now=NOW)
        assert repo.list_orders() == []

    def test_bound_customer_is_read_only(self, scoped: CustomerScopedRepository) -> None:
        """Rebinding would defeat the entire design."""
        with pytest.raises(AttributeError):
            scoped.customer_id = CARLOS  # type: ignore[misc]

    def test_apostrophe_name_is_retrievable(self, seeded_full_db: sqlite3.Connection) -> None:
        """A query built by concatenation would raise a syntax error on this row.

        That failure mode is a SQL injection hole, so the test guards a security
        property rather than a cosmetic one.
        """
        repo = CustomerScopedRepository(seeded_full_db, customer_id=APOSTROPHE, now=NOW)
        customer = repo.get_customer()
        assert customer is not None
        assert "'" in customer.full_name


# ------------------------------------------------------------------- cross-customer


class TestCrossCustomerIsolation:
    """R1: the agent may only reach data belonging to the ticket's customer."""

    def test_cannot_read_another_customers_order(self, scoped: CustomerScopedRepository) -> None:
        """Maria's repository must not return Carlos's order."""
        assert scoped.get_order(order_id=CARLOS_PENDING) is None

    def test_listing_never_includes_another_customers_orders(
        self, scoped: CustomerScopedRepository
    ) -> None:
        assert all(order.customer_id == MARIA for order in scoped.list_orders())

    def test_two_repositories_see_disjoint_data(self, seeded_full_db: sqlite3.Connection) -> None:
        maria = CustomerScopedRepository(seeded_full_db, customer_id=MARIA, now=NOW)
        carlos = CustomerScopedRepository(seeded_full_db, customer_id=CARLOS, now=NOW)
        assert not {o.id for o in maria.list_orders()} & {o.id for o in carlos.list_orders()}

    def test_no_method_accepts_a_customer_identifier(self) -> None:
        """The structural guarantee, asserted directly.

        A reviewer can conclude cross-customer access is impossible from the signatures
        alone. This encodes that reasoning so it cannot rot: adding a `customer_id`
        parameter to any scoped method fails here, forcing a deliberate decision.
        """
        import inspect

        for name, method in inspect.getmembers(CustomerScopedRepository, inspect.isfunction):
            if name.startswith("_"):
                continue
            params = set(inspect.signature(method).parameters)
            assert "customer_id" not in params, f"{name} takes a customer_id"


class TestNoExistenceLeak:
    """CWE-204, Observable Response Discrepancy.

    "Does not exist" and "belongs to someone else" must be indistinguishable. If they
    differed, trying a thousand identifiers would reveal which ones exist -- leaking
    information without returning a single row.

    Same reason a login says "username or password incorrect" instead of "that
    password is wrong".
    """

    def test_missing_and_forbidden_orders_are_indistinguishable(
        self, scoped: CustomerScopedRepository
    ) -> None:
        does_not_exist = scoped.get_order(order_id="ORD-9999999")
        belongs_to_carlos = scoped.get_order(order_id=CARLOS_PENDING)
        assert does_not_exist is belongs_to_carlos is None

    def test_neither_case_raises(self, scoped: CustomerScopedRepository) -> None:
        """Distinct exception types would leak the same information as distinct values."""
        assert scoped.get_order(order_id="ORD-9999999") is None
        assert scoped.get_order(order_id=CARLOS_PENDING) is None


# ------------------------------------------------------------------------- lateness


class TestLateness:
    """Lateness is resolved by the repository, never by the agent.

    LLMs are unreliable at date arithmetic, and there is no reason to make one derive
    what the repository can state as a fact.
    """

    def test_an_overdue_order_reports_its_lateness(self, scoped: CustomerScopedRepository) -> None:
        order = scoped.get_order(order_id=MARIA_DELAYED)
        assert order is not None
        assert order.is_late is True
        assert order.days_late == 5

    def test_a_not_yet_due_order_is_not_late(self, scoped: CustomerScopedRepository) -> None:
        order = scoped.get_order(order_id=MARIA_SHIPPED)
        assert order is not None
        assert order.is_late is False
        assert order.days_late is None

    def test_a_delivered_order_is_never_late(self, scoped: CustomerScopedRepository) -> None:
        """The finding from inspecting the generated data.

        ORD-1002 passed its promised date by 20 days *and was delivered*. Reporting "20
        days late" would be an absurd answer built on correct data -- the hardest kind
        of bug to attribute, because the data checks out.
        """
        order = scoped.get_order(order_id=MARIA_DELIVERED)
        assert order is not None
        assert order.status == OrderStatus.DELIVERED
        assert order.is_late is False
        assert order.days_late is None

    def test_lateness_follows_the_injected_clock(self, seeded_full_db: sqlite3.Connection) -> None:
        """The clock is a parameter, so a test can move it.

        Injecting it rather than reading the system clock is what keeps evaluation
        reproducible, and it also makes this test possible at all.
        """
        later = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
        repo = CustomerScopedRepository(seeded_full_db, customer_id=MARIA, now=later)
        order = repo.get_order(order_id=MARIA_DELAYED)
        assert order is not None
        assert order.days_late == 36

    def test_money_is_formatted_from_integer_cents(self, scoped: CustomerScopedRepository) -> None:
        order = scoped.get_order(order_id=MARIA_SHIPPED)
        assert order is not None
        assert order.total_amount_cents == 7250
        assert order.total_display == "72.50 USD"


# -------------------------------------------------------------------- ticket writing


class TestTicketWriting:
    """The only write in the project, and the forensic record of the agent's actions."""

    def test_creates_a_plain_ticket(self, scoped: CustomerScopedRepository) -> None:
        ticket = scoped.create_ticket(
            summary="Customer asks about the status of ORD-1003",
            priority=TicketPriority.LOW,
        )
        assert ticket.id == "TCK-0001"
        assert ticket.customer_id == MARIA
        assert ticket.status == TicketStatus.OPEN
        assert ticket.escalated is False

    def test_creates_an_escalated_ticket(self, scoped: CustomerScopedRepository) -> None:
        ticket = scoped.create_ticket(
            summary="Order ORD-1001 delayed; customer demands a refund",
            priority=TicketPriority.HIGH,
            escalation=Escalation(
                category=EscalationCategory.REFUND_REQUEST,
                reason="Customer is angry and asks for money back",
            ),
        )
        assert ticket.status == TicketStatus.ESCALATED
        assert ticket.escalated is True
        assert ticket.escalation is not None
        assert ticket.escalation.category == EscalationCategory.REFUND_REQUEST

    def test_ticket_is_attributed_to_the_bound_customer(
        self, seeded_full_db: sqlite3.Connection
    ) -> None:
        """The agent cannot open a ticket in someone else's name.

        There is no parameter for it -- attribution comes from the binding.
        """
        repo = CustomerScopedRepository(seeded_full_db, customer_id=CARLOS, now=NOW)
        ticket = repo.create_ticket(
            summary="A ticket that must belong to Carlos", priority=TicketPriority.LOW
        )
        assert ticket.customer_id == CARLOS

    def test_tickets_are_readable_back(self, scoped: CustomerScopedRepository) -> None:
        """Reading back what the agent wrote is how escalation metrics are measured."""
        scoped.create_ticket(summary="First ticket for Maria", priority=TicketPriority.LOW)
        scoped.create_ticket(
            summary="Second ticket, this one escalated",
            priority=TicketPriority.URGENT,
            escalation=Escalation(
                category=EscalationCategory.ANGRY_CUSTOMER, reason="Threatening to leave"
            ),
        )
        tickets = scoped.list_tickets()
        assert len(tickets) == 2
        assert [t.escalated for t in tickets] == [False, True]

    def test_a_customer_only_sees_their_own_tickets(
        self, seeded_full_db: sqlite3.Connection
    ) -> None:
        maria = CustomerScopedRepository(seeded_full_db, customer_id=MARIA, now=NOW)
        carlos = CustomerScopedRepository(seeded_full_db, customer_id=CARLOS, now=NOW)
        maria.create_ticket(summary="Only Maria should see this", priority=TicketPriority.LOW)
        assert len(maria.list_tickets()) == 1
        assert carlos.list_tickets() == []


# ----------------------------------------------------------------- knowledge search


class TestKnowledgeSearch:
    """Public data: no identity, nothing to scope."""

    def test_search_takes_no_customer(self, seeded_full_db: sqlite3.Connection) -> None:
        """Follows from faq_articles having no foreign key to customers.

        This is why search_knowledge_base is the one tool needing no injected identity.
        """
        import inspect

        params = set(inspect.signature(KnowledgeRepository.search).parameters)
        assert "customer_id" not in params

    @pytest.mark.parametrize(
        "hostile",
        [
            "AND",  # a bare FTS5 operator -- raises without sanitising
            'unbalanced "quote',  # unterminated string -- raises without sanitising
            "NEAR(a b)",  # FTS5 function syntax
            "' OR 1=1 --",  # injection attempt
            "***",  # only punctuation
            "",  # empty
        ],
    )
    def test_hostile_queries_do_not_raise(
        self, db_with_faq: sqlite3.Connection, hostile: str
    ) -> None:
        """Query text comes from a support ticket, so it is untrusted input.

        A bare `AND` or a stray quote is ordinary customer prose, not an exceptional
        condition. Raising would turn a normal ticket into a crashed agent turn.

        Verified: passing `AND` or an unbalanced quote straight to MATCH raises
        `OperationalError: fts5: syntax error`. The sanitiser is what prevents it, so
        this test exercises real behaviour -- unlike the two below, which were vacuous
        until this fixture existed.
        """
        KnowledgeRepository(db_with_faq).search(hostile)

    def test_finds_a_relevant_article(self, db_with_faq: sqlite3.Connection) -> None:
        """The case that justifies FTS5 over LIKE.

        "no puedo entrar en mi cuenta" shares no substring with the article's title, so
        `LIKE '%...%'` would return nothing. BM25 finds it by term relevance.
        """
        hits = KnowledgeRepository(db_with_faq).search("no puedo entrar en mi cuenta")
        assert hits
        assert hits[0].slug == "fixture-credentials"

    def test_ranks_by_relevance_descending(self, db_with_faq: sqlite3.Connection) -> None:
        """Guards the BM25 sign.

        FTS5's bm25() returns negatives, more negative meaning more relevant. The
        repository flips the sign so the agent sees "higher is better". Get that
        backwards and search silently returns the *least* relevant article first --
        a bug that produces plausible-looking wrong answers.
        """
        hits = KnowledgeRepository(db_with_faq).search(ALL_THREE_ARTICLES)
        assert len(hits) >= 2
        assert hits[0].relevance >= hits[1].relevance

    def test_respects_the_limit(self, db_with_faq: sqlite3.Connection) -> None:
        """Asserts equality, not `<=`.

        The earlier version asserted `len(...) <= 2` against an empty knowledge base, so
        it held whatever the code did -- a test that could not fail. With three matching
        articles present, `== 1` can only pass if LIMIT is genuinely applied.
        """
        repo = KnowledgeRepository(db_with_faq)
        assert len(repo.search(ALL_THREE_ARTICLES, limit=1)) == 1
        assert len(repo.search(ALL_THREE_ARTICLES, limit=3)) == 3

    def test_function_words_do_not_match(self, db_with_faq: sqlite3.Connection) -> None:
        """Regression test for a real bug this suite found.

        `_to_fts_query` originally OR-ed every word, so "garantia del procesador"
        returned the *shipping* article: "del" occurs in "salen del almacen". The agent
        would have held an irrelevant article scoring 0.53 and could have answered from
        it rather than reporting that nothing relevant existed.

        A query made only of function words must therefore find nothing.
        """
        assert KnowledgeRepository(db_with_faq).search("del la de por que") == []

    def test_accent_insensitive_search(self, db_with_faq: sqlite3.Connection) -> None:
        """`remove_diacritics 2` in the FTS5 config.

        Customers do not type accents in support tickets, so "contrasena" must find an
        article whose text says "contraseña".
        """
        db_with_faq.execute(
            "UPDATE faq_articles SET title = 'Restablecer la contraseña' WHERE id = 901"
        )
        db_with_faq.commit()
        hits = KnowledgeRepository(db_with_faq).search("contrasena")
        assert any(hit.slug == "fixture-credentials" for hit in hits)

    def test_returns_nothing_when_no_article_is_relevant(
        self, db_with_faq: sqlite3.Connection
    ) -> None:
        """The negative case, and it matters for agent behaviour.

        If every question always had a matching article, there would be no way to test
        that the agent recognises "I found nothing relevant" and asks or escalates
        instead of improvising from the best of a bad set.
        """
        assert KnowledgeRepository(db_with_faq).search("garantia del procesador") == []
