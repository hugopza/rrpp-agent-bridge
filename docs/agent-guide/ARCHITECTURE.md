# Architecture Guide

## System Boundaries

The bridge is divided into replaceable components:

1. **Inbound adapters** authenticate or validate channel input and normalize it.
2. **Event store and job queue** durably accept an event before processing begins.
3. **Worker/executor** claims jobs and generates explicit intended actions.
4. **Policy layer** produces an auditable decision for every action.
5. **Action executor** performs only actions permitted by policy and execution mode.
6. **Audit subsystem** records append-oriented, structured lifecycle facts.
7. **Private dashboard** provides authenticated operational visibility and approved controls.

The inbound service and worker MUST be independently runnable. Channel-specific code MUST end at the normalization boundary.

## Conceptual Models

The exact schema requires an accepted ADR, but implementations MUST preserve these capabilities.

### Normalized Event

- Stable internal event ID.
- Channel and channel-specific external message ID.
- Sender, recipient, and conversation/context identifiers where available.
- Subject/context and body text.
- Source receive time and bridge ingest time.
- Validated metadata and a protected raw-payload reference when retention is justified.
- Idempotency identity and processing status.

### Job

- Stable job ID and associated event ID.
- State, attempt count, availability/lease timing, and worker ownership.
- Structured last error and terminal/dead-letter status.

### Action and Policy Decision

- Stable action ID, source event/job, action type, and structured payload.
- Policy outcome: `allowed`, `blocked`, `pending_approval`, `ignored`, or `escalated`.
- Machine-readable reason and policy/rule identifier.
- Execution mode, execution state, timestamps, and external result reference if applicable.

### Audit Entry

- Actor/component, operation, related entity identifiers, timestamp, and outcome.
- Policy and mode context for every action decision or execution attempt.
- Sanitized structured error information without secrets or unnecessary message content.

## Architectural Invariants

- Persist accepted events before acknowledging successful ingestion.
- Make ingestion idempotent using channel plus external identity or an equivalent stable key.
- Assume at-least-once delivery; processing and execution MUST tolerate duplicates.
- Keep action generation separate from policy decisions and external execution.
- Record the policy decision before any permitted external side effect.
- An execution mode may further restrict policy permission but MUST NOT broaden it.
- Bound retries and preserve terminal failures for inspection.
- Treat payloads as untrusted across every boundary, including future LLM prompts.
- Do not use the audit log as the only operational data store.
- Preserve correlation IDs across event, job, action, decision, execution, and audit records.

## Safe Mode Semantics

- `shadow`: process and observe events without externally executable actions; record simulated outcomes.
- `dry-run`: generate actions and policy decisions but suppress all external execution.
- `canary`: allow execution only when policy permits and explicit test-user/condition allowlists match.
- `live`: allow policy-permitted execution; hard restrictions still apply.

In V1, `canary` and `live` execution targets only a network-free local sink. Every result is persisted with `simulated = 1`; this validates mode gating and idempotency without creating an external side effect.

Mode changes MUST be authenticated, validated, and audited. Unknown or missing modes MUST fail closed.

## Extension Rules

New channels implement the inbound adapter contract and reuse normalization, persistence, policy, auditing, and dashboard paths. New actions require a typed payload, explicit policy coverage, executor idempotency, audit events, tests, and safe behavior in every mode.

The V1 technology stack and persistence choice are accepted in ADR-0002. Future API contracts, horizontal deployment topology, retention periods, and external connectors remain `Proposed` until recorded in `DECISIONS.md`.

## Implementation Mapping

The V1 implementation maps each architectural responsibility to an explicit component:

| Architectural responsibility | RRPP V1 component | Implementation boundary |
| --- | --- | --- |
| inbound normalization | `rrpp_bridge.adapters` | Channel input becomes a `NormalizedEvent`; only the local adapter exists in V1. |
| operational workspace | `rrpp_bridge.workspace` | Exact recipient routing, venue administration, conversation lifecycle, and human review transitions. |
| durable queue | `rrpp_bridge.queue.JobQueue` | Events and jobs are committed together; channel message IDs provide idempotency. |
| conversation concurrency | job `work_key` | Related conversations serialize while unrelated conversations may proceed independently. |
| policy evaluation | `rrpp_bridge.policy.Policy` | Policy evaluates explicit intended actions and unknown actions fail closed. |
| worker execution | `rrpp_bridge.executor.Executor` | Claims durable jobs and records actions; external dispatch is intentionally absent in V1. |
| audit trail | `audit_log` through `rrpp_bridge.audit` | Lifecycle, decisions, errors, and operator operations use correlated structured entries. |
| process entry points | `rrpp_bridge.cli` | Web and worker are independently runnable processes. |
| private operations | `rrpp_bridge.web` | Authenticated operational view and local simulator, with CSRF protection. |

SQLite changes are applied through ordered, packaged migrations. Jobs use expiring leases, bounded exponential retry delays, and a terminal dead-letter state. Operators may retry or dismiss terminal jobs through authenticated, audited controls.

An RRPP worker first creates an explicit intended action, then policy decides that action, and only a future external executor may dispatch it.

Events attach to a channel-scoped conversation inside the same durable ingestion transaction. Routing is evaluated after normalization and uses only configured channel and recipient identities. A resolved conversation reopens on a new event. Drafts and escalations create review records; draft text revisions are operational records, while audit entries contain only identifiers, states, and versions.
