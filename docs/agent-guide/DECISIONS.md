# Architecture Decision Log

This file indexes architectural decisions. A high-impact decision MUST be documented before implementation. Proposed decisions do not override confirmed requirements or accepted decisions.

## Statuses

- `Proposed`: recommended and awaiting approval or implementation evidence.
- `Accepted`: approved or deliberately established by implementation.
- `Superseded`: replaced by a later ADR, with a link to its replacement.
- `Rejected`: considered and intentionally not selected.

## Decision Index

| ID | Decision | Status | Date |
| --- | --- | --- | --- |
| ADR-0001 | Use a persistent agent guide as repository context | Accepted | 2026-06-19 |
| ADR-0002 | Select the V1 application stack | Accepted | 2026-06-19 |
| ADR-0003 | Persist runtime mode and allow audited administration | Accepted | 2026-06-19 |
| ADR-0004 | Use Gmail API as the first read-only connector | Superseded | 2026-06-19 |
| ADR-0005 | Add operational venues, conversations, and human review | Superseded | 2026-06-21 |
| ADR-0006 | Operate a private single-host deployment | Accepted | 2026-06-21 |
| ADR-0007 | Add a signed inbound-only Instagram webhook | Superseded | 2026-07-02 |
| ADR-0008 | Use OpenClaw as a local draft-generation provider | Superseded | 2026-07-16 |
| ADR-0009 | Center conversations on channel accounts and customers | Accepted | 2026-07-16 |
| ADR-0010 | Add bridge-controlled Instagram delivery | Accepted | 2026-07-16 |
| ADR-0011 | Retire Gmail from the active product | Accepted | 2026-07-16 |

## ADR-0001: Persistent Agent Guide

- Status: Accepted
- Date: 2026-06-19
- Context: The project needs durable context that agents and humans consult before changing a security-sensitive bridge.
- Decision: Keep an English guide in `docs/agent-guide/`, with `AGENTS.md` as its mandatory entry point and the precedence defined in `README.md`.
- Consequences: Changes that affect architecture, constraints, decisions, workflow, or verified reusable knowledge must update their owning guide document.

## ADR-0002: V1 Application Stack

- Status: Accepted
- Date: 2026-06-19
- Context: The repository has no existing stack. V1 needs a Windows-friendly local environment, durable storage and jobs, an authenticated dashboard, tests, and later VPS deployment.
- Decision: Use Python 3.12 and its standard library for V1. Use a server-rendered WSGI dashboard, SQLite with explicit transactional job claiming, environment-based configuration, signed cookie sessions, `unittest`, and separate CLI commands for the web process and worker. Deploy both processes on a single VPS with the database on a persistent volume. Introduce third-party web or queue infrastructure only when measured requirements justify it.
- Alternatives: FastAPI and SQLAlchemy would provide a richer ecosystem but add dependencies before the bridge contracts are stable. PostgreSQL plus a distributed queue improves horizontal scale but is unnecessary for a single-owner V1.
- Consequences: Local setup is dependency-free and Windows-friendly. The application is intentionally single-host; SQLite and the WSGI server must be replaced before horizontal scaling. Schema migrations are ordered SQL files applied by the application.
- Validation: Unit and integration tests cover ingestion, idempotency, processing, policy, retry handling, authentication, and dashboard access.

## ADR-0003: Persistent Runtime Mode and Audited Administration

- Status: Accepted
- Date: 2026-06-19
- Context: Milestone 1 requires safe-mode behavior, authenticated mode changes, and recovery controls without introducing a real external connector.
- Decision: Initialize the runtime mode from `RRPP_MODE`, persist it in SQLite, and permit authenticated CSRF-protected changes from the private dashboard. Add retry and dismiss controls for terminal jobs. Prove mode behavior through a local, network-free execution sink whose records are always marked simulated.
- Alternatives: Environment-only mode changes require service restarts and cannot attribute changes to an operator. A real test connector would introduce external effects before the required security review.
- Consequences: Both web and worker read the current mode from durable state. All administrative changes and simulated execution outcomes are audited. The local sink is not evidence that an external connector is safe.
- Validation: Tests cover authentication, CSRF, mode transitions, canary scope, retry/dismiss controls, and the full execution matrix.

## ADR-0004: Gmail API Read-Only Connector

- Status: Accepted
- Date: 2026-06-19
- Context: Milestone 2 needs a dedicated Gmail inbox connector with least privilege, durable ingestion, and no mailbox mutation.
- Decision: Use Google's installed-application OAuth flow and only the `gmail.readonly` scope. Store client and refresh-token material under ignored `secrets/`. Poll Gmail independently from the worker, normalize RFC email into the shared event model, use Gmail message IDs for idempotency, and persist a Gmail `historyId` cursor only after all discovered messages are durably accepted or identified as duplicates.
- Alternatives: Password/app-password IMAP creates broader credential exposure and weaker API-level scope control. Push notifications add public webhook and cloud messaging infrastructure before polling behavior is proven.
- Consequences: The connector introduces official Google client libraries and one interactive browser consent. It cannot send, delete, label, archive, mark read, or otherwise mutate Gmail. Expired history cursors recover through a bounded inbox rescan with idempotent ingestion.
- Validation: Parser, OAuth scope, initial sync, incremental history, cursor safety, duplicate handling, error, and dashboard visibility tests.

## ADR-0005: Operational Venues, Conversations, and Human Review

- Status: Accepted
- Date: 2026-06-21
- Context: The bridge needs an operator-oriented dashboard that groups channel events into conversations, separates work by nightlife venue, and makes drafts and escalations reviewable without enabling outbound delivery.
- Decision: Model venues as operational entities, not security tenants. Resolve a venue after adapter normalization through exact channel-recipient routing rules, leaving unmatched conversations unassigned. Group messages using each channel's stable conversation identity and never merge identities across channels. Drafts require human review, may be edited with version history, and approval only marks them prepared; no review operation sends externally. Keep the append-oriented audit history and expose bounded, cursor-paginated views.
- Alternatives: Free-form venue tags would limit future configuration and metrics. Keyword routing would treat untrusted message content as operational input. Cross-channel identity merging would be unreliable and privacy-sensitive. Allowing approval to send would require new connector scopes and a separate external-effects security review.
- Consequences: The dashboard gains authenticated CSRF-protected venue, conversation, and review controls. Existing events are backfilled into conversations by `work_key`; existing assigned conversations remain stable unless an operator changes them. A single dashboard administrator can view all venues.
- Validation: Migration, routing, conversation lifecycle, review transitions, audit redaction, pagination, authentication, CSRF, and no-external-execution tests.

## ADR-0006: Private Single-Host Operations and Recovery

- Status: Accepted
- Date: 2026-06-21
- Context: The bridge needs observable long-running services, recoverable SQLite data, and a VPS-ready deployment without exposing the private dashboard publicly.
- Decision: Run web, worker, Gmail poller, and maintenance as separate processes on one host with a shared persistent SQLite volume. Persist sanitized heartbeats and backup metadata. Create verified SQLite-native daily and monthly backups, optionally export them with `age` public-key encryption, and permit restoration only through an explicit offline CLI workflow. Bind the dashboard to loopback and access it through an SSH tunnel. Container runtime processes use least privilege and normal startup never applies pending migrations.
- Alternatives: A public reverse proxy increases the attack surface and requires a separate public-deployment review. PostgreSQL and distributed supervision are unnecessary for the current single-owner load. Dashboard restore controls would expose a destructive operation to the web. Same-disk backups alone do not address host loss.
- Consequences: SQLite remains limited to one host. The owner must move encrypted exports off-host and retain the `age` private identity outside the VPS. Docker is optional for Windows development but becomes the documented VPS runtime.
- Validation: Heartbeat thresholds, error redaction, WAL-safe backup, retention, corruption detection, encrypted export, offline restoration, container configuration, dashboard privacy, and full regression tests.

## ADR-0007: Signed Inbound-Only Instagram Webhook

- Status: Accepted
- Date: 2026-07-02
- Context: Instagram DM ingestion requires a public HTTPS callback while the operational dashboard must remain private and no external response path is authorized.
- Decision: Run a dedicated WSGI ingress application that exposes only `/webhooks/instagram`, requires Meta verification on GET and `X-Hub-Signature-256` HMAC validation on POST, and fails closed unless explicitly enabled with complete environment configuration. Normalize supported text messages into the shared durable queue, group them by recipient and sender, route only by exact configured recipient account, and retain a bounded allowlisted webhook receipt. Do not create an Instagram Graph API client or outbound executor.
- Alternatives: Adding the route to the dashboard application would risk publishing authenticated operational routes. Accepting unsigned payloads would allow event forgery. Polling, scraping, or browser automation would bypass the official integration and are prohibited.
- Consequences: Deployment needs a narrow HTTPS reverse-proxy route to the ingress service. Instagram content remains untrusted and drafts require human review. Unsupported webhook event types are acknowledged and audited without creating work.
- Validation: Tests cover verification, signatures, malformed input, normalization, routing, duplicate delivery, the worker/review flow, and absence of outbound network calls even in live mode.

## ADR-0008: Local OpenClaw Draft Provider

- Status: Accepted
- Date: 2026-07-16
- Context: The bridge currently creates deterministic placeholder replies. The owner approved using a local OpenClaw agent named `rrpp` to generate contextual drafts while retaining durable processing, human review, policy enforcement, and the existing no-send boundary.
- Decision: Introduce an agent-provider interface and an OpenClaw implementation that calls the loopback-only OpenAI-compatible HTTP endpoint with a bounded timeout and authenticated bearer token. Select `openclaw/rrpp`, use one stable session identity per bridge conversation, and send bounded recent history plus explicitly configured venue knowledge as untrusted context. Prefer one schema-validated `propose_draft` tool result; accept a bounded non-empty text result as a compatibility fallback because the installed ChatGPT backend may not emit caller-defined tools. OpenClaw has no Instagram credentials or delivery capability. Every successful proposal remains `pending_approval`; provider unavailability or invalid output creates a sanitized manual-review escalation. The deterministic provider remains the disabled-state fallback.
- Alternatives: Calling OpenClaw directly from the worker would couple queue processing to one runtime. Using the full WebSocket control protocol adds unnecessary transport complexity. Calling an LLM provider directly would bypass the approved OpenClaw agent workspace. Giving OpenClaw an Instagram channel would bypass bridge policy and audit controls.
- Consequences: The OpenClaw Chat Completions endpoint must be explicitly enabled on a dedicated loopback Gateway, protected with a token, and the `rrpp` agent must have tools and channel delivery disabled. The Gateway hop is local but a configured remote model provider remains a customer-data egress boundary. Model output remains untrusted and cannot execute actions. The bridge stores no Gateway secret outside environment configuration and never writes prompts, message bodies, venue knowledge, tokens, or raw provider errors to audit records.
- Validation: Unit tests use fake providers and mocked HTTP transport to cover structured and bounded-text drafts, bounded context, authentication redaction, timeout, HTTP failure, malformed output, manual escalation, simulator and Instagram flows, and continued suppression of all external execution in `shadow` mode.

## ADR-0009: Account-Centered Conversations and Structured Agent Decisions

- Status: Accepted
- Date: 2026-07-16
- Context: One customer may compare multiple venues, events, and offers inside one Instagram conversation. Binding a conversation to one venue prevents accurate catalog-wide answers and makes venue routing an incorrect ownership boundary.
- Decision: Identify a conversation by channel, receiving account, and external customer. Keep venues, events, offers, links, availability, and verified timestamps in a structured bridge-owned catalog. OpenClaw receives bounded conversation history and a bounded catalog snapshot, then returns only a schema-validated decision: `reply`, `ask_clarification`, `human_required`, or `ignore`. Referenced catalog items must exist in the supplied snapshot. OpenClaw receives no database credentials, arbitrary query capability, or executable action interface. Plain-text compatibility output may create human review but can never qualify for automatic delivery.
- Alternatives: Sending the whole catalog on every request scales poorly. Giving the Gateway direct database tools increases prompt-injection and credential risk. Keeping one venue per conversation cannot support comparisons.
- Consequences: Existing conversation IDs remain stable, while venue assignment becomes legacy metadata. Commercial facts move out of free-form prompts over time. The bridge, not the model, decides policy, mode, idempotency, pause state, and delivery eligibility.
- Validation: Migration and tests cover identity backfill, multi-venue catalog context, strict output validation, invalid references, hard escalation rules, and compatibility fallback behavior.
- Supersedes: ADR-0005 and the generation contract in ADR-0008.

## ADR-0010: Bridge-Controlled Instagram Delivery

- Status: Accepted
- Date: 2026-07-16
- Context: The owner requires an operational demo in which safe OpenClaw replies and authenticated human replies can be sent to customers who initiated an Instagram conversation.
- Decision: Add a dedicated official Instagram Send API client owned by the bridge worker. OpenClaw never receives the Instagram access token and cannot call the channel. A real send requires a schema-valid response, explicit policy `allowed`, an unpaused conversation, a configured receiving account, a current `canary` or `live` mode, and a durable idempotent delivery record created before the network call. `shadow` and `dry-run` never send. Canary is restricted by the existing sender allowlist. Human dashboard responses use the same delivery queue, CSRF, audit, and idempotency path. Ambiguous transport results fail closed for human reconciliation rather than blind retry.
- Alternatives: Giving OpenClaw an Instagram binding bypasses bridge policy and audit. Sending directly from the dashboard couples a request to an external side effect. Treating approval as an immediate network call weakens recovery and duplicate protection.
- Consequences: The Instagram token remains environment-only. Real customer content may leave the bridge through both the configured model provider and Meta. Automatic replies remain restricted by hard business and safety rules. Deployment must run the delivery-capable worker and use only the official API.
- Validation: Tests mock all HTTP calls and cover mode suppression, canary scope, policy blocks, pause state, idempotency, success, definite failures, ambiguous failures, human authorship, and absence of credentials in audit/errors.
- Supersedes: ADR-0007 and the no-delivery boundary in ADR-0008.

## ADR-0011: Retire Gmail from the Active Product

- Status: Accepted
- Date: 2026-07-16
- Context: Gmail is no longer part of the intended product and should not add processes, credentials, UI, or dependencies to the Instagram-focused deployment.
- Decision: Remove Gmail adapter, poller, CLI commands, process definitions, configuration, dependencies, UI, and connector tests in phases. Preserve historical migrations and shared generic tables so existing databases remain upgradeable. Do not automatically delete historical Gmail events or local ignored credentials; deletion requires a separate explicit retention action.
- Alternatives: Leaving Gmail optional continues maintenance and operational ambiguity. Deleting old migrations breaks deployed databases.
- Consequences: The active runtime becomes web, Instagram ingress, worker, and maintenance. Historical Gmail rows remain readable until a separately approved purge.
- Validation: Fresh and upgraded databases migrate, the package has no Google runtime dependencies, active UI and commands omit Gmail, and the full remaining suite passes.
- Supersedes: ADR-0004.

## ADR-0012: Separate Instagram Webhook and Send Account Identifiers

- Status: Accepted
- Date: 2026-07-16
- Context: The live Instagram Login integration delivers signed webhook messages with a receiver identifier that differs from the professional account identifier returned by the Graph API for outbound Send API calls. Reusing one setting caused valid inbound DMs to be acknowledged but ignored after outbound configuration was corrected.
- Decision: Configure `INSTAGRAM_WEBHOOK_ACCOUNT_ID` as the exact receiver accepted by the signed inbound normalizer and retain `INSTAGRAM_BUSINESS_ACCOUNT_ID` exclusively as the account path for the official Send API. Require both when outbound sending is enabled. Do not infer either identifier from message content or silently accept arbitrary receiver accounts.
- Alternatives: Accepting every receiver in a correctly signed payload would weaken explicit account routing. Reusing the webhook receiver for sending fails against the observed API identity. Dynamically rewriting configuration from traffic would make account authorization implicit.
- Consequences: Existing deployments without the new variable retain compatibility by falling back to the business account ID. Deployments where Meta exposes different identifiers must configure both. Neither identifier is a secret, while tokens and app secrets remain environment-only.
- Validation: Tests use distinct inbound and outbound identifiers, require complete send configuration, and confirm that a signed message for the configured webhook receiver is persisted.
- Supersedes: The single-account-identifier assumption in ADR-0007 and ADR-0010.

## ADR Template

```markdown
## ADR-NNNN: Decision title

- Status: Proposed
- Date: YYYY-MM-DD
- Context: Why a decision is needed and which constraints apply.
- Decision: The selected approach and its behavioral boundaries.
- Alternatives: Meaningful options considered and why they were not selected.
- Consequences: Operational, security, compatibility, and maintenance effects.
- Validation: Tests or evidence that demonstrate the decision works.
- Supersedes: ADR-NNNN, when applicable.
```
