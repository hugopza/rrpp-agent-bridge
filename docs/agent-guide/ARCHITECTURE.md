# Architecture Guide

## System Boundaries

The bridge is divided into replaceable components:

1. **Inbound adapters** authenticate or validate channel input and normalize it.
2. **Event store and job queue** durably accept an event before processing begins.
3. **Worker/executor** claims jobs and asks a replaceable agent provider for an explicit intended action.
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

### Conversation and Catalog

- Conversation identity is channel, receiving account, and external customer.
- Messages keep direction, author type, channel identity, delivery state, and source correlation.
- A conversation can be paused independently of the global mode.
- Venues are catalog entities, not conversation owners.
- Events, offers, prices, links, conditions, availability, and verification metadata remain structured bridge-owned data.

### Action and Policy Decision

- Stable action ID, source event/job, action type, and structured payload.
- Policy outcome: `allowed`, `blocked`, `pending_approval`, `ignored`, or `escalated`.
- Machine-readable reason and policy/rule identifier.
- Execution mode, execution state, timestamps, and external result reference if applicable.

### Delivery

- Durable delivery intent created before the external request.
- Channel, sender account, recipient, author, idempotency key, attempts, lease, and outcome.
- Definite rejection is distinguishable from an ambiguous result that needs reconciliation.

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

For local simulator events, execution remains network-free. For Instagram, `canary` and `live` may enqueue a real delivery only after a structured agent decision passes deterministic policy. `shadow` and `dry-run` always suppress delivery.

Mode changes MUST be authenticated, validated, and audited. Unknown or missing modes MUST fail closed.

## Extension Rules

New channels implement the inbound adapter contract and reuse normalization, persistence, policy, auditing, and dashboard paths. New actions require a typed payload, explicit policy coverage, executor idempotency, audit events, tests, and safe behavior in every mode.

Instagram uses a separate public ingress process. It verifies the subscription token for GET requests and the raw-body HMAC signature for POST requests before parsing. Only text messages for the configured business account become events. Their conversation identity is channel, receiving account, and external customer. Sanitized webhook receipts retain only allowlisted message fields and delivery counts.

Decision generation uses the `AgentProvider` contract. OpenClaw calls only its authenticated loopback Chat Completions endpoint and receives bounded inbound text, recent conversation history, a language hint, and a bounded structured catalog snapshot. It must return `reply`, `ask_clarification`, `human_required`, or `ignore` with a validated reason and references. Plain-text or legacy output is review-only. It receives no connector credentials and cannot dispatch actions. Provider errors become sanitized pending escalations rather than lost jobs or external effects.

The V1 technology stack and persistence choice are accepted in ADR-0002. Future API contracts, horizontal deployment topology, retention periods, and external connectors remain `Proposed` until recorded in `DECISIONS.md`.

## Implementation Mapping

The V1 implementation maps each architectural responsibility to an explicit component:

| Architectural responsibility | RRPP V1 component | Implementation boundary |
| --- | --- | --- |
| inbound normalization | `rrpp_bridge.adapters` | Local and Instagram input become the same `NormalizedEvent`. |
| operational workspace | `rrpp_bridge.workspace` | Account/customer conversations, venue catalog administration, lifecycle, pause state, and human review transitions. |
| durable queue | `rrpp_bridge.queue.JobQueue` | Events and jobs are committed together; channel message IDs provide idempotency. |
| conversation concurrency | job `work_key` | Related conversations serialize while unrelated conversations may proceed independently. |
| policy evaluation | `rrpp_bridge.policy.Policy` | Policy evaluates explicit intended actions and unknown actions fail closed. |
| agent generation | `rrpp_bridge.agent_provider` and `rrpp_bridge.openclaw_client` | A provider creates an intended action; OpenClaw is loopback-only, bounded, authenticated, and response-validated. |
| worker execution | `rrpp_bridge.executor.Executor` | Claims durable jobs, batches message bursts, records decisions, and durably queues eligible delivery. |
| Instagram delivery | `rrpp_bridge.delivery` and `rrpp_bridge.instagram_sender` | Rechecks policy and mode, then uses only Meta's official Send API and records the outcome. |
| audit trail | `audit_log` through `rrpp_bridge.audit` | Lifecycle, decisions, errors, and operator operations use correlated structured entries. |
| operations and recovery | `rrpp_bridge.operations` | Sanitized service heartbeats, verified backup/retention, optional `age` export, health evaluation, and offline restore. |
| process entry points | `rrpp_bridge.cli` | Web and worker are independently runnable processes. |
| private operations | `rrpp_bridge.web` | Authenticated operational view and local simulator, with CSRF protection. |

SQLite changes are applied through ordered, packaged migrations. Jobs use expiring leases, bounded exponential retry delays, and a terminal dead-letter state. Operators may retry or dismiss terminal jobs through authenticated, audited controls.

An RRPP worker obtains a structured decision from its configured provider, then policy decides whether it is eligible. A separate delivery executor rechecks policy, mode, pause state, conversation freshness, and idempotency before any official Instagram request. Ambiguous transport outcomes are never retried blindly.

Events attach to an account/customer conversation inside the same durable ingestion transaction. A resolved conversation reopens on a new event. Venues, events, offers, links, prices, conditions, and availability are bridge-owned catalog data available to every conversation. Drafts and escalations create review records; draft text revisions are operational records, while audit entries contain only identifiers, states, and versions.

The supported deployment remains a single host. Web, Instagram ingress, worker, OpenClaw Gateway, and maintenance share the operating environment; bridge services share SQLite on a persistent local volume. Runtime processes refuse pending migrations. Service health is durable but not a public endpoint. Backups use SQLite's online backup API, are integrity checked before retention or restore, and encrypted exports use only an owner-controlled public recipient. Restore is never exposed through the dashboard.
