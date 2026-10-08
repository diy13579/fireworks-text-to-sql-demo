import json
import tempfile
import unittest
from pathlib import Path

from src.evaluate import _load_cases, build_evaluation_report, rows_match


class RowsMatchTests(unittest.TestCase):
    def test_matches_equivalent_results(self) -> None:
        self.assertTrue(
            rows_match(
                [{"different_alias": "Prague", "average": 1.00000001}],
                [{"district": "Prague", "average_salary": 1.0}],
            )
        )

    def test_rejects_different_values_or_order(self) -> None:
        expected = [{"id": 1}, {"id": 2}]
        self.assertFalse(rows_match([{"id": 1}, {"id": 3}], expected))
        self.assertFalse(rows_match([{"id": 2}, {"id": 1}], expected))


class EvaluationReportTests(unittest.TestCase):
    def test_aggregates_latency_tokens_and_cache_usage(self) -> None:
        per_question = [
            {
                "passed": True,
                "latency_seconds": 1.0,
                "prompt_tokens": 100,
                "cached_prompt_tokens": 80,
                "completion_tokens": 20,
                "reasoning_tokens": 15,
                "total_tokens": 120,
                "api_calls": 1,
                "server_metrics_calls": 1,
                "server_time_to_first_token_seconds": 0.2,
                "server_processing_time_seconds": 0.8,
                "sqlite_execution_ms": 2.0,
            },
            {
                "passed": False,
                "latency_seconds": 3.0,
                "prompt_tokens": 50,
                "cached_prompt_tokens": 20,
                "completion_tokens": 10,
                "reasoning_tokens": 5,
                "total_tokens": 60,
                "api_calls": 2,
                "server_metrics_calls": 2,
                "server_time_to_first_token_seconds": 0.4,
                "server_processing_time_seconds": 1.2,
                "sqlite_execution_ms": 4.0,
            },
        ]

        report = build_evaluation_report(
            per_question,
            model="accounts/fireworks/models/test-model",
            session_id="test-session",
            prompt_cache_key="test-cache-key",
        )

        self.assertEqual(report["model"], "accounts/fireworks/models/test-model")
        self.assertEqual(report["session_id"], "test-session")
        self.assertEqual(report["prompt_cache_key"], "test-cache-key")
        self.assertEqual(
            report["summary"],
            {
                "count": 2,
                "passed": 1,
                "failed": 1,
                "accuracy": 0.5,
                "mean_s": 2.0,
                "p50_s": 2.0,
                "p95_s": 2.9,
                "min_s": 1.0,
                "max_s": 3.0,
                "total_s": 4.0,
            },
        )
        self.assertEqual(
            report["cache_summary"],
            {
                "prompt_tokens": 150,
                "cached_prompt_tokens": 100,
                "completion_tokens": 30,
                "total_tokens": 180,
                "cache_hit_rate": 0.6667,
                "api_calls": 3,
            },
        )
        self.assertEqual(
            report["model_summary"],
            {
                "reasoning_tokens": 20,
                "server_metrics_calls": 3,
                "server_time_to_first_token_total_s": 0.6,
                "server_time_to_first_token_p50_s": 0.3,
                "server_processing_total_s": 2.0,
                "server_processing_p50_s": 1.0,
            },
        )
        self.assertEqual(
            report["sqlite_summary"],
            {
                "count": 2,
                "mean_ms": 3.0,
                "p50_ms": 3.0,
                "p95_ms": 3.9,
                "max_ms": 4.0,
                "max_end_to_end_share": 0.002,
            },
        )
        self.assertEqual(report["per_question"], per_question)

    def test_load_cases_rejects_missing_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps([{"id": "missing-fields"}]), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "is missing"):
                _load_cases(str(path))

    def test_load_cases_accepts_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text("[]", encoding="utf-8")

            self.assertEqual(_load_cases(str(path)), [])


if __name__ == "__main__":
    unittest.main()