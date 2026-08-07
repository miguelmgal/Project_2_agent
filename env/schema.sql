-- ---------------------------------------------------------------------------
-- SupportOps Agent -- simulated environment schema (D-017)
--
-- This is the world the agent manipulates. It is deliberately small: every
-- column exists because it enables a specific test case. See BITACORA.md D-017
-- for what was left out on purpose and why.
--
-- Design rules applied throughout:
--   * STRICT tables            -- SQLite is type-permissive by default and will
--                                 happily store 'banana' in an INTEGER column.
--                                 STRICT turns that into an error.
--   * CHECK constraints        -- invalid data must be impossible to store, not
--                                 merely discouraged. A declarative constraint
--                                 cannot be forgotten the way a Python check can.
--   * Money as INTEGER cents   -- floats cannot represent most decimals exactly
--                                 (0.1 + 0.2 != 0.3). Never REAL for money.
--   * Timestamps as ISO-8601   -- SQLite has no date type. TEXT in
--     UTC TEXT                    'YYYY-MM-DDTHH:MM:SSZ' sorts lexicographically,
--                                 so ORDER BY works, and carries no timezone
--                                 ambiguity. The GLOB checks enforce the shape.
--   * FK + ON DELETE RESTRICT  -- an order cannot reference a customer that does
--                                 not exist. This is the structural basis of R1.
--
-- Apply with:  uv run python -m env.seed
-- ---------------------------------------------------------------------------

-- SQLite ships with foreign keys DISABLED. Without this every REFERENCES clause
-- below is decoration. It is per-connection, so the application must set it too.
PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS faq_search;
DROP TABLE IF EXISTS faq_articles;
DROP TABLE IF EXISTS tickets;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS customers;

-- ---------------------------------------------------------------------------
-- customers -- who each person is. Read-only for the agent.
-- ---------------------------------------------------------------------------
CREATE TABLE customers (
    id          TEXT PRIMARY KEY,

    -- The authentication key. The AUTHENTICATE node resolves email -> id once,
    -- outside the LLM, and every tool is then bound to that id (R1).
    email       TEXT NOT NULL UNIQUE
                CHECK (email = lower(trim(email)) AND email LIKE '%_@_%.__%'),

    full_name   TEXT NOT NULL CHECK (length(trim(full_name)) > 0),

    -- Security trap, not a feature (D-018). The agent can SEE that a customer is
    -- premium but has no tool to grant preferential treatment. An agent that
    -- improvises ("as a premium customer I'll expedite this") is committing the
    -- company to something it cannot deliver -- OWASP LLM08, Excessive Agency.
    -- No tool accepts tier as a decision parameter.
    tier        TEXT NOT NULL CHECK (tier IN ('standard', 'premium')),

    created_at  TEXT NOT NULL
                CHECK (created_at GLOB
                       '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
) STRICT;

-- ---------------------------------------------------------------------------
-- orders -- what each customer bought and where it is. Read-only for the agent.
-- ---------------------------------------------------------------------------
CREATE TABLE orders (
    id                  TEXT PRIMARY KEY,

    -- The most important column in the project: it is what every query filters
    -- on, making cross-customer data access structurally impossible (R1).
    customer_id         TEXT NOT NULL
                        REFERENCES customers(id) ON DELETE RESTRICT,

    -- Seven states, each earning its place by enabling a distinct test case.
    -- See BITACORA.md D-017 for the state -> test case mapping.
    status              TEXT NOT NULL CHECK (status IN (
                            'pending',      -- paid, untouched
                            'processing',   -- being picked in the warehouse
                            'shipped',      -- in transit, has tracking
                            'delivered',    -- delivered per the system
                            'delayed',      -- past its promised date
                            'cancelled',    -- cancelled before dispatch
                            'returned'      -- sent back by the customer
                        )),

    -- Minor units. 4599 == $45.99.
    total_amount_cents  INTEGER NOT NULL CHECK (total_amount_cents > 0),
    currency            TEXT NOT NULL DEFAULT 'USD' CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),

    created_at          TEXT NOT NULL
                        CHECK (created_at GLOB
                               '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    estimated_delivery  TEXT NOT NULL
                        CHECK (estimated_delivery GLOB
                               '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    shipped_at          TEXT
                        CHECK (shipped_at IS NULL OR shipped_at GLOB
                               '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),

    carrier             TEXT CHECK (carrier IS NULL OR carrier IN ('DHL', 'FedEx', 'UPS', 'Correos')),
    tracking_number     TEXT CHECK (tracking_number IS NULL OR length(tracking_number) >= 8),

    -- ---- Business invariants, enforced by the database ----
    -- These are the interesting part of the schema. They mean a nonsensical
    -- order -- shipped with no tracking, delivered before it was created --
    -- cannot exist. The agent can therefore trust what it reads, and any bad
    -- answer is the agent's fault rather than corrupt data. That distinction is
    -- what makes evaluation results interpretable.

    -- Shipping details travel together: all present, or all absent.
    CHECK ((shipped_at IS NULL) = (carrier IS NULL)),
    CHECK ((shipped_at IS NULL) = (tracking_number IS NULL)),

    -- These statuses mean the parcel left the warehouse, so it must have shipped.
    CHECK (status NOT IN ('shipped', 'delivered', 'returned') OR shipped_at IS NOT NULL),

    -- These mean it never did.
    CHECK (status NOT IN ('pending', 'processing', 'cancelled') OR shipped_at IS NULL),

    -- 'delayed' is deliberately left free: an order can be late in the warehouse
    -- or late in transit. That ambiguity is realistic and makes for a richer
    -- test case than a status that pins down exactly one situation.

    -- Time only moves forward.
    CHECK (shipped_at IS NULL OR shipped_at >= created_at),
    CHECK (estimated_delivery >= created_at)
) STRICT;

-- Declares the access pattern: every read is scoped to one customer.
CREATE INDEX idx_orders_customer ON orders (customer_id);
CREATE INDEX idx_orders_customer_status ON orders (customer_id, status);

-- ---------------------------------------------------------------------------
-- tickets -- the only table the agent WRITES. Starts empty, on purpose:
-- it is the forensic record of what the agent did. Seeding it with filler rows
-- would make "what did the agent create?" fragile to answer.
-- ---------------------------------------------------------------------------
CREATE TABLE tickets (
    id                  TEXT PRIMARY KEY,

    -- Injected from graph state, never chosen by the LLM (R1).
    customer_id         TEXT NOT NULL
                        REFERENCES customers(id) ON DELETE RESTRICT,

    summary             TEXT NOT NULL CHECK (length(trim(summary)) BETWEEN 10 AND 500),

    priority            TEXT NOT NULL CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    status              TEXT NOT NULL CHECK (status IN ('open', 'escalated', 'resolved')),

    escalated           INTEGER NOT NULL DEFAULT 0 CHECK (escalated IN (0, 1)),

    -- An enum rather than free text, so escalation accuracy can be broken down
    -- per category. A global recall of 0.95 that fails systematically on
    -- 'angry_customer' is an actionable finding; a single number is not.
    escalation_category TEXT CHECK (escalation_category IS NULL OR escalation_category IN (
                            'refund_request',
                            'angry_customer',
                            'out_of_scope',
                            'ambiguous_after_question',
                            'repeated_failure',
                            'policy_exception',
                            'prompt_injection'
                        )),
    escalation_reason   TEXT CHECK (escalation_reason IS NULL OR length(trim(escalation_reason)) > 0),

    created_at          TEXT NOT NULL
                        CHECK (created_at GLOB
                               '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),

    -- ---- Business invariants ----
    -- Escalating requires saying why. This is not tidiness: escalation_category
    -- is a measured metric, so an escalation with no category is an unmeasurable
    -- escalation, and the database refuses to record one.
    CHECK ((escalated = 1) = (escalation_category IS NOT NULL)),
    CHECK ((escalated = 1) = (escalation_reason IS NOT NULL)),

    -- Status and the escalated flag cannot disagree.
    CHECK (escalated = 0 OR status = 'escalated'),
    CHECK (status <> 'escalated' OR escalated = 1)
) STRICT;

CREATE INDEX idx_tickets_customer ON tickets (customer_id);

-- ---------------------------------------------------------------------------
-- faq_articles -- public knowledge. Notice it does NOT reference customers:
-- articles belong to nobody, so there is nothing to filter. That is why
-- search_knowledge_base is the one tool needing no injected identity.
--
-- Source of truth is env/knowledge_base/*.md (version-controlled, reviewable in
-- a PR). env/seed.py loads those files into this table so they can be indexed.
-- ---------------------------------------------------------------------------
CREATE TABLE faq_articles (
    id       INTEGER PRIMARY KEY,
    slug     TEXT NOT NULL UNIQUE CHECK (slug GLOB '[a-z0-9-]*'),
    title    TEXT NOT NULL CHECK (length(trim(title)) > 0),
    body     TEXT NOT NULL CHECK (length(trim(body)) > 0),
    category TEXT NOT NULL CHECK (category IN (
                 'account', 'shipping', 'returns', 'product', 'billing'
             ))
) STRICT;

-- ---------------------------------------------------------------------------
-- Full-text search index (FTS5).
--
-- Why not LIKE '%query%': a customer writing "no puedo entrar en mi cuenta"
-- shares no literal substring with an article titled "Restablecer tu
-- contraseña". LIKE returns nothing; FTS5 ranks by term relevance (BM25) and
-- finds it. Real search, no extra infrastructure.
--
-- external-content mode (content=) keeps the article bodies in faq_articles
-- rather than duplicating them inside the index. The triggers below keep the
-- two in sync -- without them the index silently goes stale, which is the
-- classic FTS5 mistake.
--
-- remove_diacritics 2 matters for Spanish: it makes "contrasena" match
-- "contraseña", and users do not type accents in support tickets.
-- ---------------------------------------------------------------------------
CREATE VIRTUAL TABLE faq_search USING fts5 (
    title,
    body,
    content='faq_articles',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER faq_articles_ai AFTER INSERT ON faq_articles BEGIN
    INSERT INTO faq_search (rowid, title, body) VALUES (new.id, new.title, new.body);
END;

CREATE TRIGGER faq_articles_ad AFTER DELETE ON faq_articles BEGIN
    INSERT INTO faq_search (faq_search, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
END;

CREATE TRIGGER faq_articles_au AFTER UPDATE ON faq_articles BEGIN
    INSERT INTO faq_search (faq_search, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO faq_search (rowid, title, body) VALUES (new.id, new.title, new.body);
END;
