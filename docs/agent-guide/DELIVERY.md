# Delivery Guide

## Priority Order

1. Correct persistence and idempotent ingestion.
2. Explicit processing, action, and policy boundaries.
3. Safe execution modes and comprehensive auditing.
4. Authenticated operational visibility.
5. Connector and agent sophistication.

## Milestone 1: Bridge Foundation

Deliver the complete local simulator flow, normalized model, durable jobs, independent worker, explicit actions, policy decisions, safe modes, audit log, authenticated dashboard, structured errors, bounded retries, failed-job visibility, tests, and local run documentation.

Acceptance requires demonstrating the V1 Definition of Done in `PROJECT_BRIEF.md`, including duplicate-event handling and suppression of external execution in safe modes.

Status: Completed on 2026-06-19. The delivered foundation includes ordered migrations, lease recovery, bounded backoff, a simulated local execution sink, durable mode control, authenticated recovery controls, correlated detail views, and end-to-end tests. Final evidence: 18 automated tests, Python bytecode compilation, wheel/sdist builds, editable installation, CLI and HTTP smoke checks, clean diff validation, and repository scans for secrets and forbidden external references.

## Milestone 2: Read-Only Email Connector

Add a dedicated-inbox adapter using least-privilege environment credentials. Persist before marking ingestion successful, normalize into the existing event model, and display email events in the dashboard.

It MUST NOT send, delete, archive, label, or otherwise mutate email. Email bodies and headers remain untrusted input.

Status: Historical and retired from the active product on 2026-07-16. Migration 003 remains for upgrade compatibility, but the adapter, poller, credentials, dependencies, CLI commands, process, UI, and tests are no longer active.

## Milestone 3: External Channel Readiness

Validate adapter contracts for official Instagram and WhatsApp APIs, ticketing webhooks/imports, click tracking, and sales reporting. Do not implement a connector until its official integration path, security model, and operational ownership are understood.

Instagram increment status: Inbound webhook completed on 2026-07-02 and bridge-controlled outbound delivery completed on 2026-07-16. Signed DM events enter the common queue; structured safe decisions may use Meta's official Send API in `canary` or `live`. Production activation still requires Meta configuration, public HTTPS ingress, required permissions, and applicable Meta review.

## Operational Workspace Increment

Status: Superseded on 2026-07-16 by account/customer conversations and a global commercial catalog. The dashboard overview is operational rather than an unbounded activity feed; full activity remains cursor-paginated.

## Reliable Operations Increment

Status: Completed on 2026-06-21 and updated on 2026-07-16. Worker and maintenance expose durable sanitized heartbeats in the private dashboard. SQLite-native backups are verified, retained as seven daily and three monthly copies, optionally exported with `age`, and restorable only through an offline confirmed CLI flow with a pre-restore safety copy. Compose defines web, worker, Instagram ingress, maintenance, and migration services; the dashboard binds to loopback.

## Local OpenClaw Draft Increment

Status: Superseded on 2026-07-16 by the structured decision and delivery increment below. The provider boundary, loopback authentication, bounded context, no-tools agent, and sanitized failure behavior remain in force.

Validation covers local-only configuration, structured response parsing, timeout and HTTP errors, provider failure escalation, venue context and history, simulator and Instagram worker paths, pending review creation, and continued external-execution suppression.

## Structured Decision and Instagram Delivery Increment

Status: Completed on 2026-07-16. Conversations are identified by channel, receiving account, and external customer. Venues, events, offers, links, conditions, availability, and verification metadata form a bridge-owned catalog available across conversations. OpenClaw returns one validated decision and never receives channel credentials or delivery tools. Deterministic policy allows only bounded safe replies or clarifications; sensitive, unknown, invalid, or paused cases require a person.

Eligible Instagram responses create a durable delivery before the official Meta request. The delivery executor rechecks mode, policy, pause state, freshness, and idempotency. Human dashboard replies use the same queue. Ambiguous results pause the bot for reconciliation instead of retrying blindly. Message bursts debounce and supersede older jobs so a conversation receives one answer to the latest accumulated context.

Validation includes 61 automated tests, bytecode compilation, migration of the real database with a pre-migration backup, a live authenticated OpenClaw structured-contract check, and empty-queue verification before process startup.

## Cross-Milestone Quality Gates

- Relevant automated tests pass and failure paths are covered.
- Security requirements and hard restrictions remain enforced.
- Schema/configuration changes include migration and rollback considerations.
- Operational behavior is observable without exposing secrets or unnecessary personal data.
- Documentation and accepted decisions match the delivered behavior.
- Windows local development remains supported and later VPS deployment remains feasible.
