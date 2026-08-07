"""Typed models the repository returns.

Why models instead of raw `sqlite3.Row`: a Row is a mapping that carries whatever
columns the query selected. Add a sensitive column to the schema tomorrow and it
leaks into a tool response without anyone deciding to expose it. An explicit model
means every field the agent can see was written down on purpose.

`Order` is also where derived values live -- `is_late` and `days_late` are computed
once here rather than in each tool. Two reasons that matter:

1. **The LLM never does date arithmetic.** Models are unreliable at it, and there is
   no reason to make one derive what the tool can state as a fact.
2. **Lateness is meaningless for terminal statuses.** A delivered order is not "20
   days late" -- it was delivered. Reporting otherwise produces an absurd answer from
   correct data, which is the hardest kind of bug to attribute.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"


def parse_timestamp(value: str) -> datetime:
    """Parse the schema's ISO-8601 UTC shape.

    The schema enforces the format with a GLOB constraint, so anything reaching here
    is already well-formed -- this cannot silently accept a different shape.
    """
    return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=UTC)


class OrderStatus(StrEnum):
    """The seven order statuses. Mirrors the schema's CHECK constraint.

    A mismatch between this and the schema is caught by
    `test_order_statuses_are_readable_from_the_schema`, which reads the allowed values
    out of the DDL rather than trusting this list.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    DELAYED = "delayed"
    CANCELLED = "cancelled"
    RETURNED = "returned"

    @property
    def is_terminal(self) -> bool:
        """Whether the order's journey has ended, so lateness no longer applies."""
        return self in {OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.RETURNED}

    @property
    def has_dispatched(self) -> bool:
        """Whether the parcel has left the warehouse.

        Note `DELAYED` is absent: an order can be late in the warehouse or late in
        transit, so the status alone does not settle it. Read `shipped_at` instead --
        that ambiguity is deliberate and is one of the richer test cases.
        """
        return self in {OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.RETURNED}


class CustomerTier(StrEnum):
    STANDARD = "standard"
    PREMIUM = "premium"


class TicketPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TicketStatus(StrEnum):
    OPEN = "open"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


class EscalationCategory(StrEnum):
    """Why a ticket was escalated.

    An enum rather than free text so escalation accuracy can be broken down per
    category. A global recall of 0.95 that fails systematically on ANGRY_CUSTOMER is
    an actionable finding; a single number is not.
    """

    REFUND_REQUEST = "refund_request"
    ANGRY_CUSTOMER = "angry_customer"
    OUT_OF_SCOPE = "out_of_scope"
    AMBIGUOUS_AFTER_QUESTION = "ambiguous_after_question"
    REPEATED_FAILURE = "repeated_failure"
    POLICY_EXCEPTION = "policy_exception"
    PROMPT_INJECTION = "prompt_injection"


class _Model(BaseModel):
    """Base for all repository return types: immutable and strict."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Customer(_Model):
    """A customer, as the agent may see them."""

    id: str
    email: str
    full_name: str
    tier: CustomerTier
    """Security trap (D-018): the agent can SEE that a customer is premium but has no
    tool to grant preferential treatment. Improvising one is OWASP LLM08, Excessive
    Agency. No tool accepts this as a decision parameter."""


class Order(_Model):
    """An order, with lateness already resolved."""

    id: str
    customer_id: str
    status: OrderStatus
    total_amount_cents: int
    currency: str
    created_at: datetime
    estimated_delivery: datetime
    shipped_at: datetime | None
    carrier: str | None
    tracking_number: str | None

    days_late: int | None
    """Days past the promised delivery date, or None when lateness does not apply.

    None means one of two different things, and the distinction matters:
      * the status is terminal (delivered/cancelled/returned) -- the journey ended
      * the order is not yet due

    `is_late` disambiguates. Computed by the repository against the frozen clock
    (D-022), never against the system clock.
    """

    is_late: bool

    @model_validator(mode="after")
    def _check_lateness_coherence(self) -> Self:
        """Terminal orders must not carry a lateness figure.

        Belt and braces: the repository computes this correctly, and this makes an
        incoherent Order unconstructible even by a future caller building one by hand
        -- for instance in a test fixture.
        """
        if self.status.is_terminal and (self.is_late or self.days_late is not None):
            msg = f"terminal order {self.id} ({self.status}) cannot be late"
            raise ValueError(msg)
        if self.is_late and self.days_late is None:
            msg = f"order {self.id} is late but carries no days_late"
            raise ValueError(msg)
        return self

    @property
    def total_display(self) -> str:
        """Format the integer cents for human output.

        Money is stored as INTEGER minor units because floats cannot represent most
        decimals exactly. Formatting happens here, at the edge, never in storage.
        """
        return f"{self.total_amount_cents / 100:.2f} {self.currency}"


class Escalation(_Model):
    """Category and reason for an escalation, as a single value.

    Deliberately one parameter rather than two optional ones. The schema requires both
    or neither (a biconditional CHECK), and with two optionals a caller can supply one
    and discover the problem at the database. As a single object the type makes the
    invalid state unconstructible.
    """

    category: EscalationCategory
    reason: str = Field(min_length=1)


class Ticket(_Model):
    """A ticket, as written by the agent. The forensic record of what it did."""

    id: str
    customer_id: str
    summary: str
    priority: TicketPriority
    status: TicketStatus
    escalation: Escalation | None
    created_at: datetime

    @property
    def escalated(self) -> bool:
        return self.escalation is not None


class FaqHit(_Model):
    """One knowledge-base search result."""

    slug: str
    title: str
    body: str
    category: str
    relevance: float
    """BM25 score from FTS5, normalised so higher is more relevant.

    Exposed to the agent on purpose: it needs to be able to conclude that nothing
    sufficiently relevant was found, rather than answering from the best of a bad set.
    A retrieval tool that hides its confidence forces the caller to trust every hit.
    """
