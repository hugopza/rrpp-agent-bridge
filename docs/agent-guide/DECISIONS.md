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
