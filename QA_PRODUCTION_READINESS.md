# QA Production Readiness Assessment

> Baseline note: this report was copied unchanged from the original proof of
> concept and records the defects that motivated the production-candidate
> hardening. See `README.md` for implemented controls and remaining release
> evidence.

Date: 2026-10-06

## Executive Decision

**Verdict: NO-GO for production.**

The repository is a credible proof of concept, but it is not a production-ready
customer-facing text-to-SQL solution. The main blockers are:

1. The configured default model is documented as retired after 2026-09-25.
2. There is no query timeout, row limit, result-size limit, or cancellation path.
3. Quality evidence is only ten development questions from one schema, and the
   fastest model produced the same silent semantic error in two of three runs.
4. The `--database` option is not truly database-agnostic because schema
   documentation is always loaded from the bundled financial dataset.
5. There is no concurrency, rate-limit, degradation, or sustained-load evidence.
6. The unit suite could not be rerun in the current environment because dependency
   restoration was blocked by a package-download TLS failure.

The solution is suitable for a controlled demo using the bundled database, with
human review of generated SQL before trusting results.

## Evidence Collected

| Check | Result | Evidence |
|---|---|---|
| Python source syntax | PASS | All 7 source/test Python files parsed successfully with `ast.parse`. |
| Editor diagnostics | PASS | No Problems-panel errors were reported for `src/` or `tests/`. |
| SQLite read-only enforcement | PASS | `PRAGMA query_only=ON`; a direct `DELETE` failed with `attempt to write a readonly database`. |
| Existing benchmark artifacts | PASS with caveats | Seven reports load as JSON and contain 10 cases each. |
| Fast-model repeatability | FAIL | GLM 5.3 Fast scored 10/10, 9/10, and 9/10; the same semantic error repeated twice. |
| P50 latency target | PARTIAL | GLM runs met the sub-3-second P50 goal, but one run had 12.824s P95. |
| Current default model availability | FAIL | README states Kimi K2.6 serverless retirement after 2026-09-25. |
| Result-volume safety | FAIL | The `trans` table has 1,056,320 rows and queries are fully materialized with no cap. |
| Packaged ZIP hygiene | PASS | 62 entries; no `.env`, `.venv`, or cache directories detected. |
| Credential-free unit tests | BLOCKED | Imports failed because dependencies were absent; `uv sync` failed downloading `tzdata` due TLS handshake failure. |

## Existing Test Coverage

The checked-in tests cover:

- Successful generated-query execution
- Stable prompt-cache key generation
- One repair after an SQLite execution error
- Follow-up history and history reset
- Basic rejection of non-`SELECT` and multi-statement SQL
- Fireworks connection-error normalization
- Retry after length-truncated output
- Float-tolerant ordered result comparison
- Evaluation latency/token/cache aggregation

Important missing coverage includes CLI behavior, arbitrary databases, malformed
model responses, huge results, slow queries, timeouts, cancellation, prompt
injection, concurrency, schema drift, unavailable models, rate limits, empty or
malformed benchmark files, output-write failures, and production observability.

## Critical Production Test Cases

### P0: Must Pass Before Any Production Pilot

| ID | Test case | Expected result | Current assessment |
|---|---|---|---|
| P0-01 | Start with the configured default model on a clean account | Model exists and accepts structured-output requests | **Fail risk:** default is documented as retired |
| P0-02 | Generated `SELECT * FROM trans` | Reject or cap rows before materializing/printing | **Fail:** no row/result-size limit |
| P0-03 | Deliberately expensive recursive CTE or Cartesian join | Cancel at a configured statement deadline | **Fail:** no SQLite progress handler or timeout |
| P0-04 | Fireworks request hangs beyond the CLI SLA | Abort, report timeout, and preserve CLI usability | **Not implemented/tested** |
| P0-05 | Fireworks returns HTTP 429 with retry metadata | Bounded backoff with a clear final error | **Not explicitly implemented/tested** |
| P0-06 | Fireworks returns malformed JSON, empty choices, or missing message fields | Controlled user-facing error; CLI remains alive | **Partially covered only for invalid JSON/empty content path by inspection** |
| P0-07 | Prompt injection asks to ignore rules and expose unrelated data | Query remains within an authorization-aware schema and policy | **Not implemented/tested** |
| P0-08 | User selects a different valid SQLite database | Its schema and matching documentation are loaded without bundled financial semantics | **Fail:** descriptions use a fixed bundled path |
| P0-09 | User lacks access to selected tables/columns | Restricted objects are absent from prompt and cannot be queried | **Not implemented** |
| P0-10 | 50-100 concurrent users issue mixed queries | No cross-user history leakage, connection misuse, or SLA collapse | **Not tested; current agent/connection design is single-session** |
| P0-11 | Semantically wrong but executable SQL | Detect, abstain, ask for clarification, or invoke validated fallback | **Fail:** known repeated Prague semantic error passes execution |
| P0-12 | Run a held-out set across unseen schemas and paraphrases | Meet an agreed accuracy threshold with confidence intervals | **Fail:** evidence is 10 public dev cases on one schema |

### P1: Required for Operational Readiness

| ID | Test case | Expected result |
|---|---|---|
| P1-01 | Empty, whitespace-only, very long, Unicode, and control-character questions | Reject invalid sizes cleanly; accept valid Unicode safely |
| P1-02 | Ambiguous question such as "top customers" without metric/time range | Ask a clarification rather than invent semantics |
| P1-03 | Follow-up after an intervening unrelated question | Resolve only relevant context; never bind to the wrong prior query |
| P1-04 | More than `history_size` follow-ups | Deterministic eviction with no loss of required context |
| P1-05 | SQL containing a semicolon inside a quoted string | Treat as one statement, not a false multi-statement rejection |
| P1-06 | Leading SQL comments or harmless formatting | Normalize safely or return a precise validation error |
| P1-07 | `WITH` query containing unsafe or unsupported constructs | Parse the statement and reject by AST/authorizer policy |
| P1-08 | Duplicate column aliases in result rows | Preserve data without silent key overwrite |
| P1-09 | NULL, BLOB, NaN/Infinity, dates, and non-ASCII values | Stable display and JSON reporting |
| P1-10 | Database file missing, corrupt, locked, read-only, or replaced during execution | Clear error; no partial or misleading success |
| P1-11 | Description directory missing or CSV malformed | Explicit startup error identifying the exact artifact |
| P1-12 | Empty benchmark list | Produce a valid zero-case report without division by zero |
| P1-13 | Benchmark case missing `id`, `tier`, question, or expected result | Fail validation before any paid model calls |
| P1-14 | Output/report path unwritable or parent directory missing | Preserve results where possible and return a nonzero exit |
| P1-15 | Ctrl+C during model request and during database execution | Cancel promptly and close connections |
| P1-16 | API key absent, invalid, expired, or revoked | No secret disclosure; actionable authentication error |

### P2: Quality, Performance, and Release Confidence

| ID | Test case | Expected result |
|---|---|---|
| P2-01 | 100+ paraphrases per intent, including negation and comparative language | Stable execution accuracy by intent |
| P2-02 | Geographic field ambiguity (district name vs region) | Correct column selection or clarification |
| P2-03 | Dates at leap years, month/year boundaries, invalid dates, and time zones | Correct inclusive/exclusive filtering |
| P2-04 | One-to-many joins and repeated ownership records | No accidental double counting |
| P2-05 | Ties in top-N results | Deterministic secondary ordering and documented tie behavior |
| P2-06 | Empty tables and no-match queries | Correct empty semantics, especially `COUNT`, `SUM`, and `AVG` |
| P2-07 | Cold-cache, warm-cache, and cache-eviction runs | Separate latency distributions and stable quality |
| P2-08 | 1x, 10x, and 100x schema size | Bounded prompt size and predictable retrieval behavior |
| P2-09 | Sustained 30,000-query/day-equivalent load | Rate-limit, cost, error-rate, and P95/P99 targets met |
| P2-10 | Model/version upgrade | Automated regression gate against frozen and held-out suites |
| P2-11 | Dependency install on Windows, macOS, and Linux from a clean checkout | Reproducible setup and passing tests |
| P2-12 | Log/telemetry review | Correlation IDs, latency, model usage, query outcome, and redacted errors available |

## Defects and Risks

### Blockers

1. **Retired default model**
   - The default remains `accounts/fireworks/models/kimi-k2p6`.
   - The README states this model retires after 2026-09-25, before this assessment.
   - A clean default launch is therefore not a reliable production path.

2. **Unbounded query execution and output**
   - `query_db` calls `fetchall()` and converts every row to a dictionary.
   - The agent then formats or prints all returned rows.
   - The included `trans` table alone has 1,056,320 rows.
   - A valid read-only query can exhaust memory, flood the terminal, or run
     indefinitely without violating the current validator.

3. **Silent semantic errors**
   - Execution repair only activates for SQLite or safety failures.
   - Executable but incorrect SQL is returned as a confident answer.
   - The fastest candidate repeated the same semantic mistake in two runs.

4. **Insufficient generalization evidence**
   - Ten public development questions are too few for a production accuracy claim.
   - All cases use one known schema and may have influenced prompt iteration.
   - There are no held-out schemas, adversarial cases, ambiguity tests, or confidence
     intervals.

5. **Database portability mismatch**
   - `--database` changes the SQLite file, but column descriptions still come from
     `data/database_description`.
   - A customer database without that directory cannot initialize, and an unrelated
     database can be paired with incorrect bundled semantics.

### High Risks

- No explicit model-request deadline or end-to-end query deadline
- No user/role-aware schema filtering or authorization model
- No audit log suitable for investigating incorrect or sensitive queries
- No concurrency isolation/load evidence
- P95 latency instability despite acceptable P50 in the fast-model sample
- Naive semicolon-based SQL splitting can reject a valid semicolon inside a literal
- Result comparison is order-sensitive even when SQL semantics do not require order
- Benchmark input validation checks only that the top-level value is a list
- Empty benchmark data reaches a divide-by-zero path in console summary

## Recommended Release Gates

Do not approve production until all of these are met:

1. Select a currently supported model and run a clean default-path smoke test.
2. Enforce model and SQL deadlines, cancellation, maximum rows, maximum bytes, and
   safe pagination.
3. Use a real SQL parser and SQLite authorizer/progress handler rather than prefix
   and semicolon checks alone.
4. Separate schema discovery/documentation per selected database and filter it by
   the caller's permissions.
5. Add semantic validation plus abstention/clarification and a quality fallback.
6. Build a frozen held-out suite with unseen schemas, paraphrases, ambiguity,
   adversarial prompts, and regression thresholds.
7. Demonstrate production-like concurrent load with P50/P95/P99, error rate, cost,
   cache state, and rate-limit behavior.
8. Add structured, redacted telemetry and query audit records.
9. Prove clean dependency restoration and test execution on supported operating
   systems.

## Suggested Pilot Acceptance Criteria

- Execution accuracy: at least 98% on representative held-out queries
- Dangerous/unauthorized query escape rate: 0%
- Silent wrong-answer rate: below an explicitly agreed threshold, with abstention
  measured separately
- P50 under 3 seconds and an agreed P95 target under concurrent load
- 100% enforcement of row, byte, and execution-time limits
- No cross-user context leakage under concurrency
- Reproducible clean install and 100% passing automated release suite
