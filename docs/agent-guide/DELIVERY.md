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

Status: Completed on 2026-06-19 for Gmail. The connector uses installed-app OAuth with only `gmail.readonly`, persists Gmail events through the shared durable queue, advances `historyId` only after persistence, recovers expired history cursors through an idempotent bounded rescan, and exposes connector state plus Gmail events in the private dashboard. Validation includes 24 automated tests and a successful real read-only inbox synchronization.

## Milestone 3: External Channel Readiness

Validate adapter contracts for official Instagram and WhatsApp APIs, ticketing webhooks/imports, click tracking, and sales reporting. Do not implement a connector until its official integration path, security model, and operational ownership are understood.

## Cross-Milestone Quality Gates

- Relevant automated tests pass and failure paths are covered.
- Security requirements and hard restrictions remain enforced.
- Schema/configuration changes include migration and rollback considerations.
- Operational behavior is observable without exposing secrets or unnecessary personal data.
- Documentation and accepted decisions match the delivered behavior.
- Windows local development remains supported and later VPS deployment remains feasible.
