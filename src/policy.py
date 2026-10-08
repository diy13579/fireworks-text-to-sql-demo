"""Data-source onboarding and authorization policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuthorizationPolicy:
    """Tables and columns visible to one request."""

    allowed_tables: frozenset[str]
    allowed_columns: dict[str, frozenset[str]]

    def allows_table(self, table: str) -> bool:
        return table in self.allowed_tables

    def allows_column(self, table: str, column: str) -> bool:
        return self.allows_table(table) and column in self.allowed_columns.get(
            table, frozenset()
        )

    @classmethod
    def from_schema(
        cls, schema: dict[str, list[dict[str, Any]]]
    ) -> AuthorizationPolicy:
        return cls(
            allowed_tables=frozenset(schema),
            allowed_columns={
                table: frozenset(str(column["name"]) for column in columns)
                for table, columns in schema.items()
            },
        )


@dataclass(frozen=True)
class DataSourceProfile:
    """Versioned semantic and authorization metadata for a database."""

    data_source_id: str
    documentation_dir: str
    schema_version: str
    expected_schema_fingerprint: str
    documentation_version: str
    ontology_version: str
    concepts: dict[str, frozenset[str]]
    allowed_tables: frozenset[str] | None = None
    allowed_columns: dict[str, frozenset[str]] | None = None

    @classmethod
    def load(cls, path: str) -> DataSourceProfile:
        profile_path = Path(path)
        try:
            payload = json.loads(profile_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FileNotFoundError(f"Data-source profile not found: {path}") from None
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid data-source profile {path}: {error}") from error

        required = {
            "data_source_id",
            "documentation_dir",
            "schema_version",
            "expected_schema_fingerprint",
            "documentation_version",
            "ontology_version",
            "concepts",
        }
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError(
                f"Data-source profile {path} is missing: {', '.join(missing)}"
            )

        documentation_dir = Path(str(payload["documentation_dir"]))
        if not documentation_dir.is_absolute():
            documentation_dir = profile_path.parent / documentation_dir

        concepts = {
            str(name): frozenset(str(field) for field in fields)
            for name, fields in dict(payload["concepts"]).items()
        }
        allowed_tables_payload = payload.get("allowed_tables")
        allowed_columns_payload = payload.get("allowed_columns")
        return cls(
            data_source_id=str(payload["data_source_id"]),
            documentation_dir=str(documentation_dir),
            schema_version=str(payload["schema_version"]),
            expected_schema_fingerprint=str(payload["expected_schema_fingerprint"]),
            documentation_version=str(payload["documentation_version"]),
            ontology_version=str(payload["ontology_version"]),
            concepts=concepts,
            allowed_tables=(
                frozenset(str(table) for table in allowed_tables_payload)
                if allowed_tables_payload is not None
                else None
            ),
            allowed_columns=(
                {
                    str(table): frozenset(str(column) for column in columns)
                    for table, columns in dict(allowed_columns_payload).items()
                }
                if allowed_columns_payload is not None
                else None
            ),
        )

    def authorization_policy(
        self, schema: dict[str, list[dict[str, Any]]]
    ) -> AuthorizationPolicy:
        discovered = AuthorizationPolicy.from_schema(schema)
        if self.allowed_tables is None:
            return discovered

        unknown_tables = self.allowed_tables - discovered.allowed_tables
        if unknown_tables:
            raise ValueError(
                "Profile references unknown tables: "
                + ", ".join(sorted(unknown_tables))
            )

        allowed_columns: dict[str, frozenset[str]] = {}
        for table in self.allowed_tables:
            configured = (
                self.allowed_columns.get(table)
                if self.allowed_columns is not None
                else None
            )
            available = discovered.allowed_columns[table]
            if configured is None:
                allowed_columns[table] = available
                continue
            unknown_columns = configured - available
            if unknown_columns:
                raise ValueError(
                    f"Profile references unknown columns for {table}: "
                    + ", ".join(sorted(unknown_columns))
                )
            allowed_columns[table] = configured
        return AuthorizationPolicy(self.allowed_tables, allowed_columns)


def schema_fingerprint(schema: dict[str, list[dict[str, Any]]]) -> str:
    """Create a stable identifier for schema-drift detection."""
    normalized = {
        table: [
            {
                "name": str(column["name"]),
                "type": str(column["type"] or ""),
                "notnull": int(column["notnull"]),
                "pk": int(column["pk"]),
            }
            for column in columns
        ]
        for table, columns in sorted(schema.items())
    }
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return digest[:24]
