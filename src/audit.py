"""Structured, redacted query audit events."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str = "local"
    user_id: str = "local"
    conversation_id: str = "local"


@dataclass(frozen=True)
class AuditEvent:
    request_id: str
    event: str
    outcome: str
    data_source_id: str
    schema_fingerprint: str
    model: str
    prompt_version: str
    policy_version: str
    tenant_id: str
    user_id: str
    conversation_id: str
    question_hash: str
    question_length: int
    sql_fingerprint: str | None = None
    confidence: float | None = None
    rows_returned: int | None = None
    bytes_returned: int | None = None
    truncated: bool | None = None
    latency_seconds: float | None = None
    error_type: str | None = None
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp or datetime.now(timezone.utc).isoformat()
        return payload


class AuditSink:
    """Append JSON Lines events without storing raw questions or result rows."""

    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()

    def write(self, event: AuditEvent) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), ensure_ascii=True, separators=(",", ":"))
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def hash_question(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:24]
