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

### 2026-07-16 - Structured OpenClaw decisions and bridge-owned delivery

- Status: Verified
- Area: worker, policy, and Instagram delivery
- Fact: OpenClaw returns a bounded five-field decision but never receives Meta credentials or delivery tools. Only the bridge may create a durable Instagram delivery, recheck policy/mode/pause/freshness, call the official API, and record the result.
- Evidence: ADR-0009 and ADR-0010, migration 009, `agent-check`, delivery tests, and the 61-test suite.
- Implication: Model output never grants itself permission. New automatic cases require deterministic policy coverage and delivery tests.

### 2026-07-16 - Conversations are account/customer scoped

- Status: Verified
- Area: conversation and catalog model
- Fact: Conversation identity is channel, receiving account, and external customer. Venues are global catalog entities with structured events, offers, links, availability, and verification metadata; they are not required conversation owners.
- Evidence: ADR-0009, migration 009, workspace/catalog code, and migration tests.
- Implication: Do not infer or permanently assign a conversation to a venue from message text. Supply bounded verified catalog data to the agent instead.

### 2026-07-16 - Gmail is retired from the active product

- Status: Verified
- Area: runtime and dependencies
- Fact: Gmail adapter, poller, configuration, secrets, Google dependencies, CLI commands, service, active UI, and tests were removed. Historical migration 003 and stored rows remain for safe upgrades and retention.
- Evidence: ADR-0011, package metadata, CLI/Compose configuration, and the full suite.
- Implication: Do not reintroduce email concerns into shared Instagram paths. A future email product requires a new accepted decision.

### 2026-07-16 - OpenClaw was a draft-only provider

- Status: Superseded
- Area: worker and agent generation
- Fact: The worker can call the local authenticated OpenClaw agent `rrpp` through a replaceable provider contract, validates one structured draft, and converts provider failures into sanitized manual-review escalations. OpenClaw receives bounded context and has no channel credentials or delivery tools.
- Evidence: ADR-0008, migration 008, `agent_provider.py`, `openclaw_client.py`, worker tests, and the full automated suite.
- Implication: Agent sophistication may change behind the provider interface, but all outputs remain untrusted intended actions subject to policy, mode suppression, audit, and human review.

### 2026-07-16 - Venue knowledge was conversation-routed

- Status: Superseded
- Area: dashboard workspace
- Fact: Each venue stores operator-managed verified knowledge for draft generation. Venue routing still depends only on explicit channel/recipient rules, while the worker sends a language hint and the original message so the provider follows the customer's language and tone.
- Evidence: migration 008, authenticated venue form, bounded agent context, and provider-context tests.
- Implication: Do not infer a venue from message text and do not restore a fixed venue-language selector. Unknown or unsupported facts require human review rather than fabrication.

### 2026-07-16 - Venue identifiers are operator-friendly

- Status: Verified
- Area: dashboard workspace
- Fact: Creating a venue accepts a normal display name and generates its stable ASCII identifier automatically; an optional identifier is normalized with the same rules.
- Evidence: `workspace.create_venue`, the authenticated dashboard form, and `test_venue_slug_is_generated_from_normal_name_or_optional_identifier`.
- Implication: Routing remains based on exact configured recipient identities, while operators do not need to know the internal identifier format to create a venue.

### 2026-07-02 - Instagram inbound-only security boundary

- Status: Superseded
- Area: inbound connectors
- Fact: Instagram DM input runs in a dedicated WSGI application, validates Meta raw-body signatures, persists allowlisted receipts and normalized events, and has no outbound API client. Venue routing uses only the exact recipient account ID.
- Evidence: ADR-0007, migration 007, Instagram adapter/webhook tests, and the full worker-to-review integration test.
- Implication: Keep the public ingress surface separate from the dashboard; adding attachments, profile lookup, permissions, or sending requires a new scoped security review.

### 2026-06-21 - Private single-host operations

- Status: Verified
- Area: deployment and recovery
- Fact: Long-running services persist sanitized health, backups use SQLite's online API plus integrity verification, restore is offline and guarded, and the Compose dashboard port binds only to host loopback. Encrypted exports use an `age` public recipient while the private identity remains off-host.
- Evidence: migration 006, `operations.py`, operations tests, successful image build, healthy multi-service Compose smoke test, graceful-stop verification, and `docs/DEPLOYMENT.md`.
- Implication: Future deployment work must preserve one-host SQLite semantics, explicit migrations, non-public health data, off-host encrypted-copy handling, and CLI-only restore.

### 2026-06-21 - Venue-routed operational workspace

- Status: Superseded
- Area: dashboard and workflow
- Fact: Events are grouped into channel-native conversations and may be assigned to operational venues by exact recipient routes; drafts and escalations require human review, and approval never sends externally. The overview renders at most eight conversations and ten audit entries while full activity uses cursor pagination.
- Evidence: migration 004, `workspace.py`, authenticated web tests, and the complete automated suite.
- Implication: Future adapters must provide a stable conversation key and normalized recipient identity. New review controls must preserve CSRF, audit redaction, and the non-sending approval boundary until a separate external-effects ADR is accepted.

### 2026-06-19 - Initial V1 runtime and external effects

- Status: Superseded
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

### 2026-06-19 - Gmail read-only connector (retired)

- Status: Superseded
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
