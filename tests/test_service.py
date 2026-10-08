import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from src.agent import DEFAULT_MODEL
from src.audit import AuditSink
from src.policy import DataSourceProfile
from src.service import QueryRequest, QueryService
from src.utils import DEFAULT_DB_PATH


class FakeCompletions:
    def create(self, **kwargs: object) -> SimpleNamespace:
        del kwargs
        content = json.dumps(
            {
                "plan": {
                    "intent": "aggregate",
                    "entity_grain": "account",
                    "required_tables": ["account"],
                    "required_columns": ["account.account_id"],
                    "business_concepts": [],
                    "ambiguities": [],
                    "clarification_question": None,
                    "confidence": 0.99,
                },
                "sql": "SELECT COUNT(account_id) AS account_count FROM account",
            }
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=content),
                )
            ],
            usage=None,
            perf_metrics=None,
        )


class FakeClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions())


class QueryServiceTests(unittest.TestCase):
    def test_concurrent_requests_use_isolated_connections(self) -> None:
        service = QueryService(
            database_path=DEFAULT_DB_PATH,
            profile=DataSourceProfile.load("data/database_profile.json"),
            client_factory=FakeClient,
            audit_sink=AuditSink(None),
            model=DEFAULT_MODEL,
        )
        requests = [
            QueryRequest(
                question="How many accounts are there?",
                tenant_id=f"tenant-{index % 2}",
                user_id=f"user-{index}",
                conversation_id=f"conversation-{index}",
            )
            for index in range(10)
        ]

        with ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(service.query, requests))

        self.assertEqual(
            [result.rows for result in results],
            [[{"account_count": 4500}]] * 10,
        )


if __name__ == "__main__":
    unittest.main()
