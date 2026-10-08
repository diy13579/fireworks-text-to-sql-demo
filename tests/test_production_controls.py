import sqlite3
import threading
import unittest

from src.agent import RETIRED_MODELS, TextToSQLAgent
from src.audit import AuditSink
from src.execution import (
    QueryCancelledError,
    QueryLimits,
    QueryTimeoutError,
    ResultSizeError,
    execute_guarded,
)
from src.policy import AuthorizationPolicy, DataSourceProfile
from src.sql_guard import SQLGuard, SQLPolicyError
from src.utils import load_db


class SQLGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE t (id INTEGER, value TEXT)")
        self.conn.execute("CREATE TABLE secret (id INTEGER)")
        self.policy = AuthorizationPolicy(
            allowed_tables=frozenset({"t"}),
            allowed_columns={"t": frozenset({"id", "value"})},
        )
        self.guard = SQLGuard(self.conn, self.policy)

    def tearDown(self) -> None:
        self.conn.close()

    def test_accepts_semicolon_inside_literal_and_adds_hard_limit(self) -> None:
        guarded = self.guard.validate_and_bound(
            "SELECT id FROM t WHERE value = ';'", max_rows=10
        )

        self.assertIn("LIMIT 11", guarded.sql)
        self.assertEqual(guarded.referenced_tables, frozenset({"t"}))

    def test_rejects_writes_multiple_statements_and_unknown_tables(self) -> None:
        with self.assertRaisesRegex(SQLPolicyError, "SELECT"):
            self.guard.validate_and_bound("DELETE FROM t", max_rows=10)
        with self.assertRaisesRegex(SQLPolicyError, "Exactly one"):
            self.guard.validate_and_bound("SELECT id FROM t; SELECT 1", max_rows=10)
        with self.assertRaisesRegex(SQLPolicyError, "not authorized"):
            self.guard.validate_and_bound("SELECT id FROM secret", max_rows=10)


class GuardedExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)")
        self.conn.executemany(
            "INSERT INTO t(value) VALUES (?)",
            [("short",), ("x" * 200,)],
        )
        self.policy = AuthorizationPolicy(
            allowed_tables=frozenset({"t"}),
            allowed_columns={"t": frozenset({"id", "value"})},
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_enforces_byte_limit(self) -> None:
        with self.assertRaises(ResultSizeError):
            execute_guarded(
                self.conn,
                "SELECT value FROM t WHERE id = 2",
                policy=self.policy,
                limits=QueryLimits(max_rows=10, max_bytes=20),
            )

    def test_authorizer_blocks_direct_write(self) -> None:
        with self.assertRaises(sqlite3.DatabaseError):
            execute_guarded(
                self.conn,
                "DELETE FROM t",
                policy=self.policy,
                limits=QueryLimits(),
            )

    def test_honors_preexisting_cancellation(self) -> None:
        cancellation = threading.Event()
        cancellation.set()
        with self.assertRaises(QueryCancelledError):
            execute_guarded(
                self.conn,
                "WITH RECURSIVE n(x) AS "
                "(VALUES(0) UNION ALL SELECT x + 1 FROM n WHERE x < 1000000) "
                "SELECT sum(x) FROM n",
                policy=self.policy,
                limits=QueryLimits(sql_timeout_seconds=1),
                cancellation=cancellation,
            )

    def test_interrupts_expensive_statement_at_deadline(self) -> None:
        with self.assertRaises(QueryTimeoutError):
            execute_guarded(
                self.conn,
                "WITH RECURSIVE n(x) AS "
                "(VALUES(0) UNION ALL SELECT x + 1 FROM n WHERE x < 100000000) "
                "SELECT sum(x) FROM n",
                policy=self.policy,
                limits=QueryLimits(sql_timeout_seconds=0.001),
            )


class OnboardingPolicyTests(unittest.TestCase):
    def test_rejects_retired_model(self) -> None:
        conn = load_db()
        self.addCleanup(conn.close)

        with self.assertRaisesRegex(ValueError, "retired"):
            TextToSQLAgent(
                conn,
                client=object(),
                model=next(iter(RETIRED_MODELS)),
                audit_sink=AuditSink(None),
            )

    def test_rejects_schema_drift(self) -> None:
        conn = load_db()
        self.addCleanup(conn.close)
        profile = DataSourceProfile.load("data/database_profile.json")
        drifted = DataSourceProfile(
            data_source_id=profile.data_source_id,
            documentation_dir=profile.documentation_dir,
            schema_version=profile.schema_version,
            expected_schema_fingerprint="incorrect",
            documentation_version=profile.documentation_version,
            ontology_version=profile.ontology_version,
            concepts=profile.concepts,
        )

        with self.assertRaisesRegex(ValueError, "does not match"):
            TextToSQLAgent(
                conn,
                client=object(),
                profile=drifted,
                audit_sink=AuditSink(None),
            )


if __name__ == "__main__":
    unittest.main()
