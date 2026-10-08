"""Governed CLI entry point. Run with: uv run cli."""

from __future__ import annotations

import argparse
import sqlite3

from src.agent import DEFAULT_MODEL, DEFAULT_PROFILE_PATH, AgentResult, TextToSQLAgent
from src.audit import AuditSink
from src.execution import QueryLimits
from src.policy import DataSourceProfile
from src.sql_guard import ClarificationRequired
from src.utils import DEFAULT_DB_PATH, load_db


def main() -> None:
    args = _parse_args()
    conn: sqlite3.Connection | None = None

    try:
        profile = DataSourceProfile.load(args.profile)
        conn = load_db(args.database)
        agent = TextToSQLAgent(
            conn,
            model=args.model,
            profile=profile,
            limits=QueryLimits(
                sql_timeout_seconds=args.sql_timeout,
                max_rows=args.max_rows,
                max_bytes=args.max_bytes,
            ),
            audit_sink=AuditSink(args.audit_log),
        )
    except (FileNotFoundError, sqlite3.Error, ValueError) as error:
        print(f"Startup error: {error}")
        raise SystemExit(1) from error

    print("Fireworks Governed Text-to-SQL")
    print(f"Model: {agent.model}")
    print(f"Data source: {agent.profile.data_source_id}")
    print(f"Schema fingerprint: {agent.schema_fingerprint}")
    print("Ask a question, type 'more' for the next page, or 'exit'.")

    last_result: AgentResult | None = None
    try:
        while True:
            try:
                question = input("\nsql> ").strip()
            except EOFError:
                print()
                break

            if question.lower() in {"exit", "quit"}:
                break
            if not question:
                continue

            try:
                if question.lower() == "more":
                    if last_result is None or last_result.next_offset is None:
                        print("No additional page is available.")
                        continue
                    last_result = agent.continue_result(last_result)
                else:
                    last_result = agent.ask(question, page_size=args.page_size)
                _print_result(last_result)
            except ClarificationRequired as error:
                print(f"Clarification required: {error.question}")
            except (RuntimeError, ValueError, sqlite3.Error) as error:
                print(f"Error: {error}")
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        conn.close()

    print("Goodbye.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ask governed questions about an onboarded SQLite database."
    )
    parser.add_argument(
        "--database",
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE_PATH,
        help="Approved data-source profile matching the selected database",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-rows", type=int, default=200)
    parser.add_argument("--max-bytes", type=int, default=1_000_000)
    parser.add_argument("--sql-timeout", type=float, default=5.0)
    parser.add_argument("--audit-log", default="logs/audit.jsonl")
    return parser.parse_args()


def _print_result(result: AgentResult) -> None:
    print("\nSQL:")
    print(result.sql)
    print(f"\nConfidence: {result.plan.confidence:.2f}")
    print("Results:")
    if result.rows:
        _print_rows(result.rows)
    else:
        print("No matching rows.")
    if result.truncated:
        print(
            f"Result truncated at {len(result.rows)} rows / "
            f"{result.bytes_returned} bytes. Type 'more' to continue."
        )
    repair_note = ", repaired" if result.repaired else ""
    print(f"\nAnswer: {result.answer}")
    print(
        f"Request: {result.request_id} | Latency: "
        f"{result.latency_seconds:.2f}s{repair_note}"
    )


def _print_rows(rows: list[dict[str, object]]) -> None:
    columns = list(rows[0])
    widths = {
        column: max(
            len(column),
            *(len(str(row.get(column, ""))) for row in rows),
        )
        for column in columns
    }
    print(" | ".join(column.ljust(widths[column]) for column in columns))
    print("-+-".join("-" * widths[column] for column in columns))
    for row in rows:
        print(
            " | ".join(
                str(row.get(column, "")).ljust(widths[column])
                for column in columns
            )
        )


if __name__ == "__main__":
    main()
