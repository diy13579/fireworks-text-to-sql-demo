"""Run the text-to-SQL agent against the public development benchmark."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agent import (
    DEFAULT_MODEL,
    DEFAULT_PROFILE_PATH,
    ModelUsage,
    TextToSQLAgent,
)
from src.audit import AuditSink
from src.policy import DataSourceProfile
from src.utils import DEFAULT_DB_PATH, load_db

DEFAULT_QUESTIONS_PATH = "data/dev_questions_with_answers.json"
DEFAULT_ANSWERS_PATH = "dev_answers.json"
DEFAULT_REPORT_PATH = "evaluation_results.json"
LATENCY_DEFINITION = (
    "Wall-clock seconds for agent.ask(): natural-language question through SQL "
    "generation (including truncation and repair retries), SQLite execution, and "
    "deterministic answer formatting."
)


def main() -> None:
    args = _parse_args()
    cases = _load_cases(args.questions)
    conn = load_db(args.database)
    agent = TextToSQLAgent(
        conn,
        model=args.model,
        profile=DataSourceProfile.load(args.profile),
        audit_sink=AuditSink(args.audit_log),
    )
    session_id = args.session_id or datetime.now(timezone.utc).strftime(
        "text2sql-%Y%m%dT%H%M%SZ"
    )
    answers: dict[str, dict[str, str]] = {}
    report: list[dict[str, Any]] = []

    try:
        for index, case in enumerate(cases, 1):
            case_id = case["id"]
            print(f"[{index}/{len(cases)}] {case_id}: {case['question']}")
            agent.reset_history()
            try:
                result = agent.ask(case["question"])
                passed = rows_match(result.rows, case["expected_result"])
                answers[case_id] = {"sql": result.sql, "answer": result.answer}
                report.append(
                    {
                        "id": case_id,
                        "tier": case["tier"],
                        "passed": passed,
                        "status": "PASS" if passed else "FAIL",
                        "latency_seconds": round(result.latency_seconds, 3),
                        "sqlite_execution_ms": round(
                            result.sqlite_execution_seconds * 1000, 3
                        ),
                        "confidence": result.plan.confidence,
                        "truncated": result.truncated,
                        "bytes_returned": result.bytes_returned,
                        "repaired": result.repaired,
                        **_usage_fields(result.usage),
                        "sql": result.sql,
                        "answer": result.answer,
                        "actual_result": result.rows,
                        "expected_result": case["expected_result"],
                        "error": None,
                    }
                )
                print(
                    f"  {'PASS' if passed else 'FAIL'} in "
                    f"{result.latency_seconds:.2f}s"
                )
            except Exception as error:
                answers[case_id] = {"sql": "", "answer": f"Error: {error}"}
                report.append(
                    {
                        "id": case_id,
                        "tier": case["tier"],
                        "passed": False,
                        "status": "ERROR",
                        "latency_seconds": None,
                        "sqlite_execution_ms": None,
                        "repaired": False,
                        **_usage_fields(agent.last_usage),
                        "sql": "",
                        "answer": "",
                        "actual_result": None,
                        "expected_result": case["expected_result"],
                        "error": str(error),
                    }
                )
                print(f"  ERROR: {error}")
    finally:
        conn.close()

    _write_json(args.output, answers)
    _write_json(
        args.report,
        build_evaluation_report(
            report,
            model=agent.model,
            session_id=session_id,
            prompt_cache_key=agent.prompt_cache_key,
        ),
    )
    _print_summary(report)


def rows_match(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> bool:
    """Compare ordered SQL values while tolerating aliases and float noise."""
    return _normalize_rows(actual) == _normalize_rows(expected)


def _normalize_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    normalized: list[list[Any]] = []
    for row in rows:
        normalized.append(
            [
                round(value, 6) if isinstance(value, float) else value
                for value in row.values()
            ]
        )
    return normalized


def _load_cases(path: str) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"Expected a list of benchmark cases in {path}.")
    required = {"id", "tier", "question", "expected_result"}
    seen_ids: set[str] = set()
    for index, case in enumerate(data):
        if not isinstance(case, dict):
            raise ValueError(f"Benchmark case {index} must be an object.")
        missing = sorted(required - case.keys())
        if missing:
            raise ValueError(
                f"Benchmark case {index} is missing: {', '.join(missing)}"
            )
        case_id = str(case["id"])
        if case_id in seen_ids:
            raise ValueError(f"Duplicate benchmark case id: {case_id}")
        seen_ids.add(case_id)
        if not isinstance(case["question"], str) or not case["question"].strip():
            raise ValueError(f"Benchmark case {case_id} has an invalid question.")
        if not isinstance(case["expected_result"], list):
            raise ValueError(
                f"Benchmark case {case_id} expected_result must be a list."
            )
    return data


def _write_json(path: str, data: Any) -> None:
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=True, default=str) + "\n",
        encoding="utf-8",
    )


def build_evaluation_report(
    report: list[dict[str, Any]],
    *,
    model: str,
    session_id: str,
    prompt_cache_key: str,
) -> dict[str, Any]:
    """Build a self-contained benchmark report with latency and token totals."""
    return {
        "definition": LATENCY_DEFINITION,
        "model": model,
        "session_id": session_id,
        "prompt_cache_key": prompt_cache_key,
        "summary": _build_summary(report),
        "cache_summary": _build_cache_summary(report),
        "model_summary": _build_model_summary(report),
        "sqlite_summary": _build_sqlite_summary(report),
        "per_question": report,
    }


def _usage_fields(usage: ModelUsage) -> dict[str, int | float]:
    cache_hit_rate = (
        usage.cached_prompt_tokens / usage.prompt_tokens
        if usage.prompt_tokens
        else 0.0
    )
    return {
        "prompt_tokens": usage.prompt_tokens,
        "cached_prompt_tokens": usage.cached_prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "total_tokens": usage.total_tokens,
        "cache_hit_rate": round(cache_hit_rate, 4),
        "api_calls": usage.api_calls,
        "server_metrics_calls": usage.server_metrics_calls,
        "server_time_to_first_token_seconds": (
            round(usage.server_time_to_first_token_seconds, 3)
            if usage.server_metrics_calls
            else None
        ),
        "server_processing_time_seconds": (
            round(usage.server_processing_time_seconds, 3)
            if usage.server_metrics_calls
            else None
        ),
    }


def _build_summary(report: list[dict[str, Any]]) -> dict[str, int | float | None]:
    passed = sum(1 for item in report if item["passed"])
    latencies = [
        float(item["latency_seconds"])
        for item in report
        if item["latency_seconds"] is not None
    ]
    summary: dict[str, int | float | None] = {
        "count": len(report),
        "passed": passed,
        "failed": len(report) - passed,
        "accuracy": round(passed / len(report), 4) if report else 0.0,
        "mean_s": None,
        "p50_s": None,
        "p95_s": None,
        "min_s": None,
        "max_s": None,
        "total_s": None,
    }
    if latencies:
        summary.update(
            {
                "mean_s": round(statistics.fmean(latencies), 3),
                "p50_s": round(_percentile(latencies, 0.50), 3),
                "p95_s": round(_percentile(latencies, 0.95), 3),
                "min_s": round(min(latencies), 3),
                "max_s": round(max(latencies), 3),
                "total_s": round(sum(latencies), 3),
            }
        )
    return summary


def _build_cache_summary(report: list[dict[str, Any]]) -> dict[str, int | float]:
    prompt_tokens = sum(int(item.get("prompt_tokens") or 0) for item in report)
    cached_prompt_tokens = sum(
        int(item.get("cached_prompt_tokens") or 0) for item in report
    )
    completion_tokens = sum(
        int(item.get("completion_tokens") or 0) for item in report
    )
    total_tokens = sum(int(item.get("total_tokens") or 0) for item in report)
    api_calls = sum(int(item.get("api_calls") or 0) for item in report)
    cache_hit_rate = (
        cached_prompt_tokens / prompt_tokens if prompt_tokens else 0.0
    )
    return {
        "prompt_tokens": prompt_tokens,
        "cached_prompt_tokens": cached_prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cache_hit_rate": round(cache_hit_rate, 4),
        "api_calls": api_calls,
    }


def _build_model_summary(
    report: list[dict[str, Any]],
) -> dict[str, int | float | None]:
    ttft_values = [
        float(item["server_time_to_first_token_seconds"])
        for item in report
        if item.get("server_time_to_first_token_seconds") is not None
    ]
    processing_values = [
        float(item["server_processing_time_seconds"])
        for item in report
        if item.get("server_processing_time_seconds") is not None
    ]
    return {
        "reasoning_tokens": sum(
            int(item.get("reasoning_tokens") or 0) for item in report
        ),
        "server_metrics_calls": sum(
            int(item.get("server_metrics_calls") or 0) for item in report
        ),
        "server_time_to_first_token_total_s": (
            round(sum(ttft_values), 3) if ttft_values else None
        ),
        "server_time_to_first_token_p50_s": (
            round(_percentile(ttft_values, 0.50), 3) if ttft_values else None
        ),
        "server_processing_total_s": (
            round(sum(processing_values), 3) if processing_values else None
        ),
        "server_processing_p50_s": (
            round(_percentile(processing_values, 0.50), 3)
            if processing_values
            else None
        ),
    }


def _build_sqlite_summary(
    report: list[dict[str, Any]],
) -> dict[str, int | float | None]:
    sqlite_values = [
        float(item["sqlite_execution_ms"])
        for item in report
        if item.get("sqlite_execution_ms") is not None
    ]
    shares = [
        float(item["sqlite_execution_ms"])
        / (float(item["latency_seconds"]) * 1000)
        for item in report
        if item.get("sqlite_execution_ms") is not None
        and item.get("latency_seconds")
    ]
    return {
        "count": len(sqlite_values),
        "mean_ms": round(statistics.fmean(sqlite_values), 3)
        if sqlite_values
        else None,
        "p50_ms": round(_percentile(sqlite_values, 0.50), 3)
        if sqlite_values
        else None,
        "p95_ms": round(_percentile(sqlite_values, 0.95), 3)
        if sqlite_values
        else None,
        "max_ms": round(max(sqlite_values), 3) if sqlite_values else None,
        "max_end_to_end_share": round(max(shares), 6) if shares else None,
    }


def _percentile(values: list[float], probability: float) -> float:
    """Return a linearly interpolated percentile for a non-empty sample."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _print_summary(report: list[dict[str, Any]]) -> None:
    passed = sum(1 for item in report if item["passed"])
    latencies = [
        item["latency_seconds"]
        for item in report
        if item["latency_seconds"] is not None
    ]
    accuracy = passed / len(report) if report else 0.0
    print(f"\nAccuracy: {passed}/{len(report)} ({accuracy:.0%})")
    if latencies:
        print(f"P50 latency: {statistics.median(latencies):.2f}s")

    for tier in sorted({item["tier"] for item in report}):
        tier_items = [item for item in report if item["tier"] == tier]
        tier_passed = sum(1 for item in tier_items if item["passed"])
        print(f"Tier {tier}: {tier_passed}/{len(tier_items)}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the agent on dev questions.")
    parser.add_argument("--questions", default=DEFAULT_QUESTIONS_PATH)
    parser.add_argument("--database", default=DEFAULT_DB_PATH)
    parser.add_argument("--output", default=DEFAULT_ANSWERS_PATH)
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--profile", default=DEFAULT_PROFILE_PATH)
    parser.add_argument("--audit-log", default="logs/evaluation-audit.jsonl")
    parser.add_argument(
        "--session-id",
        help="Optional run identifier (default: UTC timestamp-based identifier)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()