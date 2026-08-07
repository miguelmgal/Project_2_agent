"""Database layer.

The ONLY place in the project that speaks SQL. Tools call the repository; they do
not build queries (CLAUDE.md section 6).
"""

from supportops.db.connection import SCHEMA_PATH, apply_schema, connect
from supportops.db.repository import (
    AuthRepository,
    CustomerScopedRepository,
    KnowledgeRepository,
)

__all__ = [
    "SCHEMA_PATH",
    "AuthRepository",
    "CustomerScopedRepository",
    "KnowledgeRepository",
    "apply_schema",
    "connect",
]
