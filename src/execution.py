"""Bounded, cancellable SQLite execution."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

from src.policy import AuthorizationPolicy
from src.sql_guard import build_authorizer


class QueryTimeoutError(sqlite3.OperationalError):
    """Raised when a statement exceeds its execution deadline."""


class QueryCancelledError(sqlite3.OperationalError):
    """Raised when a caller cancels a running statement."""


class ResultSizeError(sqlite3.DataError):
    """Raised when one row exceeds the complete result byte budget."""


@dataclass(frozen=True)
class QueryLimits:
    sql_timeout_seconds: float = 5.0
    max_rows: int = 200
    max_bytes: int = 1_000_000
    fetch_batch_size: int = 100

    def __post_init__(self) -> None:
        if self.sql_timeout_seconds <= 0:
            raise ValueError("sql_timeout_seconds must be positive.")
        if self.max_rows <= 0:
            raise ValueError("max_rows must be positive.")
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive.")
        if self.fetch_batch_size <= 0:
            raise ValueError("fetch_batch_size must be positive.")


@dataclass(frozen=True)
class QueryPage:
    rows: list[dict[str, Any]]
    truncated: bool
    next_offset: int | None
    bytes_returned: int
    execution_seconds: float


def execute_guarded(
    conn: sqlite3.Connection,
    sql: str,
    *,
    policy: AuthorizationPolicy,
    limits: QueryLimits,
    offset: int = 0,
    cancellation: threading.Event | None = None,
) -> QueryPage:
    """Execute one already-parsed query without unbounded materialization."""
    cancellation = cancellation or threading.Event()
    deadline = time.monotonic() + limits.sql_timeout_seconds
    timed_out = False
    cancelled = False

    def progress() -> int:
        nonlocal timed_out, cancelled
        cancelled = cancellation.is_set()
        timed_out = time.monotonic() >= deadline
        return int(cancelled or timed_out)

    conn.set_authorizer(build_authorizer(policy))
    conn.set_progress_handler(progress, 1_000)
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    bytes_returned = 0
    truncated = False

    try:
        cursor = conn.execute(sql)
        columns = _unique_column_names(
            [str(description[0]) for description in cursor.description or ()]
        )
        while True:
            batch = cursor.fetchmany(limits.fetch_batch_size)
            if not batch:
                break
            for raw_row in batch:
                if len(rows) >= limits.max_rows:
                    truncated = True
                    break
                row = dict(zip(columns, raw_row))
                row_bytes = len(
                    json.dumps(
                        row, ensure_ascii=True, default=str, separators=(",", ":")
                    ).encode("utf-8")
                )
                if row_bytes > limits.max_bytes and not rows:
                    raise ResultSizeError(
                        "A single result row exceeds the configured byte limit."
                    )
                if bytes_returned + row_bytes > limits.max_bytes:
                    truncated = True
                    break
                rows.append(row)
                bytes_returned += row_bytes
            if truncated:
                break
    except sqlite3.OperationalError as error:
        if cancelled:
            raise QueryCancelledError("SQL execution was cancelled.") from error
        if timed_out:
            raise QueryTimeoutError(
                f"SQL execution exceeded {limits.sql_timeout_seconds:.3f}s."
            ) from error
        raise
    finally:
        conn.set_progress_handler(None, 0)
        conn.set_authorizer(None)

    return QueryPage(
        rows=rows,
        truncated=truncated,
        next_offset=offset + len(rows) if truncated else None,
        bytes_returned=bytes_returned,
        execution_seconds=time.perf_counter() - started,
    )


def _unique_column_names(columns: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    unique: list[str] = []
    for column in columns:
        counts[column] = counts.get(column, 0) + 1
        suffix = f"_{counts[column]}" if counts[column] > 1 else ""
        unique.append(f"{column}{suffix}")
    return unique
