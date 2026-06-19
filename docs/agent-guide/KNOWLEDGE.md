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
