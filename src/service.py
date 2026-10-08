"""Concurrency-safe service boundary with per-request SQLite connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.agent import AgentResult, TextToSQLAgent
from src.audit import AuditSink, RequestContext
from src.execution import QueryLimits
from src.policy import AuthorizationPolicy, DataSourceProfile
from src.utils import get_schema, load_db


@dataclass(frozen=True)
class QueryRequest:
    question: str
    tenant_id: str
    user_id: str
    conversation_id: str
    page_size: int = 100


PolicyResolver = Callable[
    [QueryRequest, dict[str, list[dict[str, Any]]]],
    AuthorizationPolicy,
]


class QueryService:
    """Create an isolated connection and agent for every service request."""

    def __init__(
        self,
        *,
        database_path: str,
        profile: DataSourceProfile,
        client_factory: Callable[[], Any],
        policy_resolver: PolicyResolver | None = None,
        limits: QueryLimits | None = None,
        audit_sink: AuditSink | None = None,
        model: str | None = None,
    ) -> None:
        self.database_path = database_path
        self.profile = profile
        self.client_factory = client_factory
        self.policy_resolver = policy_resolver
        self.limits = limits or QueryLimits()
        self.audit_sink = audit_sink or AuditSink(None)
        self.model = model

    def query(self, request: QueryRequest) -> AgentResult:
        conn = load_db(self.database_path)
        try:
            schema = get_schema(conn)
            policy = (
                self.policy_resolver(request, schema)
                if self.policy_resolver is not None
                else self.profile.authorization_policy(schema)
            )
            agent = TextToSQLAgent(
                conn,
                client=self.client_factory(),
                model=self.model,
                profile=self.profile,
                policy=policy,
                limits=self.limits,
                audit_sink=self.audit_sink,
                request_context=RequestContext(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    conversation_id=request.conversation_id,
                ),
                history_size=0,
            )
            return agent.ask(request.question, page_size=request.page_size)
        finally:
            conn.close()
