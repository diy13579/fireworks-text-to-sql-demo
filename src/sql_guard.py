"""Native SQLite parsing, policy enforcement, bounding, and semantics."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.policy import AuthorizationPolicy, DataSourceProfile

if TYPE_CHECKING:
    from src.agent import QueryPlan


class SQLPolicyError(ValueError):
    """Raised when generated SQL violates a deterministic policy."""


class SemanticValidationError(ValueError):
    """Raised when SQL does not implement its declared logical plan."""


class ClarificationRequired(ValueError):
    """Raised when the planner identifies unresolved ambiguity."""

    def __init__(self, question: str) -> None:
        super().__init__(question)
        self.question = question


@dataclass(frozen=True)
class GuardedSQL:
    sql: str
    source_sql: str
    referenced_tables: frozenset[str]
    referenced_columns: frozenset[str]


@dataclass
class AuthorizationTrace:
    tables: set[str] = field(default_factory=set)
    columns: set[str] = field(default_factory=set)
    denied_object: str | None = None
    denied_action: int | None = None


class SQLGuard:
    """Use SQLite's parser to enforce policy and add safe outer pagination."""

    def __init__(
        self, conn: sqlite3.Connection, policy: AuthorizationPolicy
    ) -> None:
        self.conn = conn
        self.policy = policy

    def validate_and_bound(
        self, sql: str, *, max_rows: int, offset: int = 0
    ) -> GuardedSQL:
        if max_rows < 1:
            raise ValueError("max_rows must be at least 1.")
        if offset < 0:
            raise ValueError("offset cannot be negative.")

        normalized = sql.strip()
        if not normalized:
            raise SQLPolicyError("The generated SQL is empty.")
        if normalized.endswith(";"):
            normalized = normalized[:-1].rstrip()

        trace = AuthorizationTrace()
        self.conn.set_authorizer(build_authorizer(self.policy, trace))
        try:
            self.conn.execute(f"EXPLAIN QUERY PLAN {normalized}").fetchall()
        except sqlite3.Error as error:
            if "one statement at a time" in str(error).casefold():
                raise SQLPolicyError(
                    "Exactly one SQL statement is required."
                ) from error
            if trace.denied_object:
                raise SQLPolicyError(
                    f"Database object is not authorized: {trace.denied_object}"
                ) from error
            if trace.denied_action is not None:
                raise SQLPolicyError(
                    "Only authorized read-only SELECT queries are allowed."
                ) from error
            raise SQLPolicyError(f"Invalid SQLite query: {error}") from error
        finally:
            self.conn.set_authorizer(None)

        if not trace.tables:
            raise SQLPolicyError("The query must reference an authorized table.")

        bounded = (
            "SELECT * FROM (\n"
            f"{normalized}\n"
            ") AS _bounded_query "
            f"LIMIT {max_rows + 1} OFFSET {offset}"
        )
        self.conn.set_authorizer(build_authorizer(self.policy))
        try:
            self.conn.execute(f"EXPLAIN QUERY PLAN {bounded}").fetchall()
        except sqlite3.Error as error:
            raise SQLPolicyError(
                f"Unable to apply safe result pagination: {error}"
            ) from error
        finally:
            self.conn.set_authorizer(None)

        return GuardedSQL(
            sql=bounded,
            source_sql=normalized,
            referenced_tables=frozenset(trace.tables),
            referenced_columns=frozenset(trace.columns),
        )


def build_authorizer(
    policy: AuthorizationPolicy,
    trace: AuthorizationTrace | None = None,
) -> Any:
    """Create a fail-closed SQLite authorizer for generated statements."""
    denied_actions = {
        getattr(sqlite3, name)
        for name in (
            "SQLITE_ALTER_TABLE",
            "SQLITE_ANALYZE",
            "SQLITE_ATTACH",
            "SQLITE_CREATE_INDEX",
            "SQLITE_CREATE_TABLE",
            "SQLITE_CREATE_TEMP_INDEX",
            "SQLITE_CREATE_TEMP_TABLE",
            "SQLITE_CREATE_TEMP_TRIGGER",
            "SQLITE_CREATE_TEMP_VIEW",
            "SQLITE_CREATE_TRIGGER",
            "SQLITE_CREATE_VIEW",
            "SQLITE_DELETE",
            "SQLITE_DETACH",
            "SQLITE_DROP_INDEX",
            "SQLITE_DROP_TABLE",
            "SQLITE_DROP_TEMP_INDEX",
            "SQLITE_DROP_TEMP_TABLE",
            "SQLITE_DROP_TEMP_TRIGGER",
            "SQLITE_DROP_TEMP_VIEW",
            "SQLITE_DROP_TRIGGER",
            "SQLITE_DROP_VIEW",
            "SQLITE_INSERT",
            "SQLITE_PRAGMA",
            "SQLITE_REINDEX",
            "SQLITE_TRANSACTION",
            "SQLITE_UPDATE",
        )
        if hasattr(sqlite3, name)
    }

    def authorize(
        action: int,
        argument_one: str | None,
        argument_two: str | None,
        database: str | None,
        trigger: str | None,
    ) -> int:
        del database, trigger
        if action in denied_actions:
            if trace is not None:
                trace.denied_action = action
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ:
            table = argument_one or ""
            column = argument_two or ""
            allowed = (
                policy.allows_table(table)
                if not column
                else policy.allows_column(table, column)
            )
            if not allowed:
                if trace is not None:
                    trace.denied_object = (
                        f"{table}.{column}" if column else table
                    )
                return sqlite3.SQLITE_DENY
            if trace is not None:
                trace.tables.add(table)
                if column:
                    trace.columns.add(f"{table}.{column}")
        return sqlite3.SQLITE_OK

    return authorize


def verify_semantics(
    plan: QueryPlan,
    guarded: GuardedSQL,
    profile: DataSourceProfile,
    *,
    minimum_confidence: float,
) -> None:
    """Check that executable SQL implements the model's declared plan."""
    if plan.ambiguities:
        clarification = plan.clarification_question or (
            "Please clarify: " + "; ".join(plan.ambiguities)
        )
        raise ClarificationRequired(clarification)
    if plan.confidence < minimum_confidence:
        raise ClarificationRequired(
            plan.clarification_question
            or "I cannot validate this interpretation confidently. Please clarify."
        )

    missing_tables = set(plan.required_tables) - set(guarded.referenced_tables)
    if missing_tables:
        raise SemanticValidationError(
            "SQL omits plan tables: " + ", ".join(sorted(missing_tables))
        )

    missing_columns = set(plan.required_columns) - set(guarded.referenced_columns)
    if missing_columns:
        raise SemanticValidationError(
            "SQL omits plan columns: " + ", ".join(sorted(missing_columns))
        )

    for concept in plan.business_concepts:
        approved_fields = profile.concepts.get(concept)
        if approved_fields is None:
            raise SemanticValidationError(
                f"Unknown business concept in plan: {concept}"
            )
        if not approved_fields.intersection(guarded.referenced_columns):
            raise SemanticValidationError(
                f"SQL does not use an approved field for concept: {concept}"
            )


def sql_fingerprint(sql: str) -> str:
    """Hash SQL for correlation without logging query literals."""
    normalized = " ".join(sql.split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:24]
