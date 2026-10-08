# Production-Candidate Implementation Record

## Objective

Harden the original Fireworks text-to-SQL demonstration without modifying the
source project or its benchmark artifacts.

## Implemented work

1. Created this isolated project copy without the original `.env`, virtual
   environment, caches, or ZIP artifacts.
2. Added versioned data-source onboarding with schema-drift detection,
   data-source-specific documentation, ontology mappings, and authorization
   allowlists.
3. Changed model output from SQL-only to a logical plan plus SQL contract.
4. Added ambiguity, confidence, required-object, and business-concept checks.
5. Replaced string safety checks with SQLite native parsing and authorization.
6. Added safe outer-query pagination and bounded incremental result fetching.
7. Added SQLite progress deadlines, cancellation, byte caps, and write denial.
8. Added explicit model timeout, total deadline, and bounded retries.
9. Added structured audit events that omit raw questions, SQL, and result rows.
10. Added a per-request service boundary for concurrent callers.
11. Added P0 regression tests and updated runtime documentation.

## Design decisions

- SQLite's native parser and authorizer are used rather than adding a parser
  dependency. This preserves dialect fidelity and avoids weakening TLS after
  dependency retrieval failed.
- Query pages are implemented by wrapping a parser-validated query rather than
  appending or editing a `LIMIT` clause.
- Both filesystem read-only mode and `PRAGMA query_only` are used as defense in
  depth.
- The model can repair deterministic plan, policy, or execution failures once,
  but ambiguity goes directly to clarification.
- Audit logs retain hashes, versions, identity, outcomes, limits, and metrics,
  but not questions, SQL literals, or result data.
- Per-user authorization is injected before schema context is built and is
  re-enforced by the SQLite authorizer during parsing and execution.

## Verification gates

The code is not considered production-certified until the remaining release
evidence listed in `README.md` is collected. In particular, ten development
questions are not sufficient proof of semantic accuracy or generalization.
