import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.agent import ModelUsage, TextToSQLAgent
from src.audit import AuditSink
from src.sql_guard import ClarificationRequired
from src.utils import load_db


class FakeRateLimitError(Exception):
    status_code = 429


FakeRateLimitError.__module__ = "openai"


def model_response(
    sql: str,
    *,
    required_tables: list[str],
    required_columns: list[str],
    business_concepts: list[str] | None = None,
    ambiguities: list[str] | None = None,
    clarification_question: str | None = None,
    confidence: float = 0.95,
    prompt_tokens: int = 0,
    cached_prompt_tokens: int = 0,
    completion_tokens: int = 0,
    reasoning_tokens: int = 0,
    server_ttft: float | None = None,
    server_processing: float | None = None,
) -> SimpleNamespace:
    content = json.dumps(
        {
            "plan": {
                "intent": "aggregate",
                "entity_grain": "record",
                "required_tables": required_tables,
                "required_columns": required_columns,
                "business_concepts": business_concepts or [],
                "ambiguities": ambiguities or [],
                "clarification_question": clarification_question,
                "confidence": confidence,
            },
            "sql": sql,
        }
    )
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_prompt_tokens),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
    )
    perf_metrics = None
    if server_ttft is not None or server_processing is not None:
        perf_metrics = {
            "server-time-to-first-token": server_ttft,
            "server-processing-time": server_processing,
        }
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=content),
            )
        ],
        usage=usage,
        perf_metrics=perf_metrics,
    )


class TextToSQLAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = load_db()
        self.client = Mock()

    def tearDown(self) -> None:
        self.conn.close()

    def test_executes_generated_query(self) -> None:
        self.client.chat.completions.create.return_value = model_response(
            "SELECT COUNT(*) AS owner_count FROM disp WHERE type = 'OWNER'",
            required_tables=["disp"],
            required_columns=["disp.type"],
            business_concepts=["account_owner"],
        )

        result = TextToSQLAgent(
            self.conn,
            client=self.client,
            prompt_cache_key="banking-schema-v1",
            audit_sink=AuditSink(None),
        ).ask("How many disposition records represent account owners?")

        self.assertEqual(result.rows, [{"owner_count": 4500}])
        self.assertEqual(result.answer, "owner_count: 4500")
        self.assertFalse(result.repaired)
        self.assertFalse(result.truncated)
        self.assertIsNone(result.next_offset)

    def test_repairs_semantically_incorrect_executable_query(self) -> None:
        self.client.chat.completions.create.side_effect = [
            model_response(
                "SELECT COUNT(a.account_id) AS account_count "
                "FROM account a JOIN district d "
                "ON a.district_id = d.district_id WHERE d.A2 = 'Prague'",
                required_tables=["account", "district"],
                required_columns=[
                    "account.account_id",
                    "account.district_id",
                    "district.district_id",
                    "district.A2",
                ],
                business_concepts=["region"],
                prompt_tokens=100,
                cached_prompt_tokens=80,
                completion_tokens=20,
                reasoning_tokens=15,
                server_ttft=0.25,
                server_processing=1.1,
            ),
            model_response(
                "SELECT COUNT(a.account_id) AS account_count "
                "FROM account a JOIN district d "
                "ON a.district_id = d.district_id WHERE d.A3 = 'Prague'",
                required_tables=["account", "district"],
                required_columns=[
                    "account.account_id",
                    "account.district_id",
                    "district.district_id",
                    "district.A3",
                ],
                business_concepts=["region"],
                prompt_tokens=120,
                cached_prompt_tokens=100,
                completion_tokens=30,
                reasoning_tokens=20,
                server_ttft=0.3,
                server_processing=1.5,
            ),
        ]

        result = TextToSQLAgent(
            self.conn,
            client=self.client,
            audit_sink=AuditSink(None),
        ).ask("How many bank accounts are located in the Prague region?")

        self.assertEqual(result.rows, [{"account_count": 554}])
        self.assertTrue(result.repaired)
        self.assertEqual(result.usage.prompt_tokens, 220)
        self.assertEqual(result.usage.cached_prompt_tokens, 180)
        self.assertEqual(result.usage.completion_tokens, 50)
        self.assertEqual(result.usage.reasoning_tokens, 35)
        self.assertEqual(result.usage.api_calls, 2)
        repair_prompt = self.client.chat.completions.create.call_args_list[1].kwargs[
            "messages"
        ][1]["content"]
        self.assertIn("SemanticValidationError", repair_prompt)

    def test_requires_clarification_for_ambiguous_plan(self) -> None:
        self.client.chat.completions.create.return_value = model_response(
            "SELECT account_id FROM account",
            required_tables=["account"],
            required_columns=["account.account_id"],
            ambiguities=["top metric is not defined"],
            clarification_question="What metric should define the top accounts?",
        )
        agent = TextToSQLAgent(
            self.conn,
            client=self.client,
            audit_sink=AuditSink(None),
        )

        with self.assertRaisesRegex(
            ClarificationRequired, "What metric should define"
        ):
            agent.ask("Show the top accounts")

        self.assertEqual(self.client.chat.completions.create.call_count, 1)

    def test_paginates_large_results_without_regeneration(self) -> None:
        self.client.chat.completions.create.return_value = model_response(
            "SELECT trans_id FROM trans ORDER BY trans_id",
            required_tables=["trans"],
            required_columns=["trans.trans_id"],
        )
        agent = TextToSQLAgent(
            self.conn,
            client=self.client,
            audit_sink=AuditSink(None),
        )

        first = agent.ask("List transaction identifiers", page_size=2)
        second = agent.continue_result(first)

        self.assertEqual(len(first.rows), 2)
        self.assertTrue(first.truncated)
        self.assertEqual(first.next_offset, 2)
        self.assertEqual(len(second.rows), 2)
        self.assertNotEqual(first.rows, second.rows)
        self.assertEqual(self.client.chat.completions.create.call_count, 1)

    def test_writes_redacted_audit_event(self) -> None:
        self.client.chat.completions.create.return_value = model_response(
            "SELECT COUNT(*) FROM disp WHERE type = 'OWNER'",
            required_tables=["disp"],
            required_columns=["disp.type"],
            business_concepts=["account_owner"],
        )
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            agent = TextToSQLAgent(
                self.conn,
                client=self.client,
                audit_sink=AuditSink(str(audit_path)),
            )
            agent.ask("Count all account owners")
            raw_event = audit_path.read_text(encoding="utf-8")
            event = json.loads(raw_event)

        self.assertNotIn("Count all account owners", raw_event)
        self.assertNotIn("OWNER", raw_event)
        self.assertIsNotNone(event["sql_fingerprint"])
        self.assertEqual(event["outcome"], "success")
        self.assertEqual(event["rows_returned"], 1)

    def test_tracks_stable_prompt_cache_key_and_usage_type(self) -> None:
        first = TextToSQLAgent(
            self.conn,
            client=self.client,
            audit_sink=AuditSink(None),
        )
        second = TextToSQLAgent(
            self.conn,
            client=self.client,
            audit_sink=AuditSink(None),
        )

        self.assertEqual(first.prompt_cache_key, second.prompt_cache_key)
        self.assertRegex(first.prompt_cache_key, r"^text2sql-[0-9a-f]{24}$")
        self.assertIsInstance(first.last_usage, ModelUsage)

    def test_retries_rate_limit_with_bounded_attempts(self) -> None:
        self.client.chat.completions.create.side_effect = [
            FakeRateLimitError("rate limited"),
            model_response(
                "SELECT COUNT(account_id) AS account_count FROM account",
                required_tables=["account"],
                required_columns=["account.account_id"],
            ),
        ]
        agent = TextToSQLAgent(
            self.conn,
            client=self.client,
            audit_sink=AuditSink(None),
            max_model_retries=1,
            retry_backoff_seconds=0,
        )

        result = agent.ask("How many accounts are there?")

        self.assertEqual(result.rows, [{"account_count": 4500}])
        self.assertEqual(result.usage.api_calls, 2)

    def test_rejects_malformed_model_response(self) -> None:
        self.client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="{not-json"),
                )
            ],
            usage=None,
            perf_metrics=None,
        )
        agent = TextToSQLAgent(
            self.conn,
            client=self.client,
            audit_sink=AuditSink(None),
        )

        with self.assertRaisesRegex(RuntimeError, "invalid structured output"):
            agent.ask("How many accounts are there?")


if __name__ == "__main__":
    unittest.main()
