# Knowledge Base

This file stores verified, reusable facts learned while building and operating the project. It is not a backlog, decision log, or place for speculative advice.

## Recording Format

```markdown
### YYYY-MM-DD - Short title

- Status: Verified | Superseded
- Area: component or concern
- Fact: concise reusable knowledge
- Evidence: file, test, command, incident, or documentation reference
- Implication: how future work should use this fact
```

Never include secrets, credentials, personal data, raw customer messages, or production identifiers.

## Verified Entries

### 2026-06-21 - Private single-host operations

- Status: Verified
- Area: deployment and recovery
- Fact: Long-running services persist sanitized health, backups use SQLite's online API plus integrity verification, restore is offline and guarded, and the Compose dashboard port binds only to host loopback. Encrypted exports use an `age` public recipient while the private identity remains off-host.
- Evidence: migration 006, `operations.py`, operations tests, successful image build, healthy multi-service Compose smoke test, graceful-stop verification, and `docs/DEPLOYMENT.md`.
- Implication: Future deployment work must preserve one-host SQLite semantics, explicit migrations, non-public health data, off-host encrypted-copy handling, and CLI-only restore.

### 2026-06-21 - Operational workspace and bounded dashboard

- Status: Verified
- Area: dashboard and workflow
- Fact: Events are grouped into channel-native conversations and may be assigned to operational venues by exact recipient routes; drafts and escalations require human review, and approval never sends externally. The overview renders at most eight conversations and ten audit entries while full activity uses cursor pagination.
- Evidence: migration 004, `workspace.py`, authenticated web tests, and the complete automated suite.
- Implication: Future adapters must provide a stable conversation key and normalized recipient identity. New review controls must preserve CSRF, audit redaction, and the non-sending approval boundary until a separate external-effects ADR is accepted.

### 2026-06-19 - V1 runtime and external effects

- Status: Verified
- Area: runtime
- Fact: V1 uses Python 3.12 standard-library services and SQLite/WAL; web and worker run separately and no external action dispatcher exists.
- Evidence: ADR-0002 and `rrpp_bridge/`.
- Implication: A real connector or outbound executor requires a new security review and accepted ADR.

### 2026-06-19 - Milestone 1 operational model

- Status: Verified
- Area: queue and execution
- Fact: Jobs use expiring leases and bounded exponential backoff; runtime mode is durable; all V1 execution outcomes use an idempotent network-free sink marked simulated.
- Evidence: migrations 002, `queue.py`, `runtime.py`, `action_executor.py`, and automated tests.
- Implication: Operators can validate recovery and all safe modes locally, but must not interpret simulated execution as connector readiness.

### 2026-06-19 - Prepared Windows local environment

- Status: Verified
- Area: local development
- Fact: The project can load an ignored BOM-compatible `.env`, run from a repository virtual environment, build wheel/sdist artifacts, and start web plus worker through `scripts/run-local.ps1` without Docker.
- Evidence: configuration tests, successful editable installation and build, CLI status, and HTTP login smoke test.
- Implication: Local onboarding requires only Python 3.12; runtime credentials and data remain ignored and machine-local.

### 2026-06-19 - Gmail read-only connector

- Status: Verified
- Area: inbound connectors
- Fact: Gmail ingestion uses only the `gmail.readonly` OAuth scope, stores credentials under ignored `secrets/`, and persists a `historyId` cursor only after durable message ingestion.
- Evidence: ADR-0004, migration 003, Gmail adapter/connector tests, and successful real inbox synchronization.
- Implication: The connector may read message content from `INBOX` but cannot mutate the mailbox; future outbound email requires a separate ADR and authorization scope.

### 2026-06-19 - Dashboard visual system

- Status: Verified
- Area: dashboard
- Fact: The private dashboard uses packaged, dependency-free CSS with design tokens, Catalan operational copy, responsive cards/tables, accessible focus states, and no external font or asset requests.
- Evidence: `rrpp_bridge/static/dashboard.css`, authenticated HTTP smoke test, packaged-wheel asset check, and web tests.
- Implication: Future dashboard views should reuse existing card, badge, grid, table, form, and record-detail patterns instead of adding isolated inline styles.

### 2026-06-19 - Repository baseline

- Status: Verified
- Area: repository
- Fact: The repository began without tracked project files or commits; only Git metadata existed.
- Evidence: initial repository inspection and Git status.
- Implication: no legacy implementation or stack convention should be assumed.

### 2026-06-19 - Canonical project name

- Status: Verified
- Area: naming
- Fact: The canonical product and repository name in documentation is `rrpp-agent-bridge`.
- Evidence: confirmed project brief.
- Implication: use this spelling in packages, services, documentation, and future deployment identifiers unless an accepted ADR says otherwise.
