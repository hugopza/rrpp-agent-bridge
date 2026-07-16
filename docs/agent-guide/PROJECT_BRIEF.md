# Project Brief

## Objective

Build `rrpp-agent-bridge`: a secure, observable, and extensible bridge between customer communication channels and future nightlife promoter agents. The first priority is dependable infrastructure, not autonomous sales behavior or a sophisticated personality.

This is a new, independently implemented project built around a durable and auditable bridge pattern.

## Core Flow

```text
inbound channel
-> normalized event
-> durable queue/job storage
-> worker/executor
-> generated action
-> policy decision
-> permitted execution or safe suppression
-> audit log
-> private dashboard
```

Inbound adapters validate and normalize data. They MUST NOT contain business logic. Workers MUST operate independently of inbound adapters, and every generated action MUST pass through policy before execution.

## Confirmed V1 Requirements

V1 MUST provide:

- A local simulator as the only required inbound channel.
- A normalized, traceable event model.
- Durable persistence before asynchronous processing.
- Independent worker/executor processing.
- Explicit action and policy decision models.
- Safe execution modes: `shadow`, `dry-run`, `canary`, and `live`.
- A safe default mode: `shadow` unless an accepted decision explicitly selects `dry-run`.
- Complete audit logging for meaningful processing and action steps.
- A private, authenticated dashboard.
- Job errors, bounded retries, and failed/dead-letter visibility.
- Environment-based configuration and secrets.
- Idempotency for repeated inbound events.
- Basic automated tests, local development setup, and clear documentation.

V1 agent behavior may be deterministic or mocked. Placeholder outcomes may include `draft_reply`, `send_configured_ticket_link`, `escalate`, and `no_action`; their value is proving the complete infrastructure flow.

## Dashboard Outcomes

The owner MUST be able to inspect:

- Current execution mode and basic metrics.
- Received events and queued, processed, and failed jobs.
- Generated, blocked, pending, and escalated actions.
- Recent policy decisions, audit activity, and structured errors.
- Whether any external action was actually attempted or executed.

Approval, rejection, replay, resolution, and log export controls are optional for V1 unless needed to satisfy failed-job or pending-action visibility.

## Non-Goals for V1

- Real Instagram or WhatsApp integration.
- Real ticket sales, click tracking, payments, or reservations.
- Automatic email replies or email mutation.
- A complex LLM prompt, autonomous promoter, or human-like avatar.
- Mass outbound messaging, browser automation, or scraping.

Email is the likely first real connector after the bridge foundation. Its first milestone MUST be read-only: ingest, persist, normalize, and display without sending, deleting, labeling, archiving, or prematurely marking messages handled.

## Hard Restrictions

The system MUST NOT:

- Confirm reservations, promise guest-list access, or make business decisions.
- Promise discounts or invent prices, dates, events, or availability.
- Manage payments or request unnecessary personal information.
- Spam users or send outbound mass messages.
- Pretend to be a specific real person.
- Bypass official APIs or depend on fragile scraping/browser automation.
- Expose the dashboard without authentication.
- Commit credentials or place secrets in prompts or logs.
- Send externally unless both the active mode and policy permit it.

## Future Readiness

The design SHOULD allow later adapters for email, Instagram DM, WhatsApp Business, ticketing webhooks, and manual dashboard actions. Ticket analytics MUST distinguish `link sent`, `link clicked`, and `ticket sold`; the system MUST never infer or fabricate a sale.

## Confirmed Operational Workspace

- Group events into channel-native conversations; never infer cross-channel identity.
- Identify each conversation by channel, receiving account, and external customer; do not bind it to one venue.
- Keep venues, events, offers, links, conditions, availability, and verification metadata in a bridge-owned catalog consultable from any conversation.
- Let OpenClaw propose a structured `reply`, `ask_clarification`, `human_required`, or `ignore` decision from bounded untrusted context.
- Editing, human reply, pause, resume, resolution, and delivery controls MUST be authenticated, CSRF-protected, and audited.
- Permit automatic Instagram replies only when policy explicitly allows them and `canary` or `live` mode permits execution.
- Keep OpenClaw free of Instagram credentials and direct channel delivery; the bridge owns every external side effect.
- Keep the audit history in the local phase; bound and paginate dashboard views instead of rendering unbounded lists.
- Use the local OpenClaw `rrpp` agent for bounded response decisions and wording. Invalid, sensitive, stale, or unsupported results require human review.

## Confirmed Operational Deployment

- Run web, Instagram ingress, worker, OpenClaw Gateway, and maintenance independently on one host.
- Keep the VPS dashboard bound to loopback and access it through an SSH tunnel.
- Persist sanitized service health without exception text, credentials, or message content.
- Create verified SQLite-native backups daily, retain seven daily and three monthly copies, and prepare public-key-encrypted exports for off-host storage.
- Keep the backup decryption identity off the VPS.
- Restore only through an explicit offline CLI operation that creates a safety backup and verifies integrity.

## V1 Definition of Done

V1 is complete when a local fake message is validated, normalized, durably persisted, independently processed into at least one action, decided by policy, fully audited, and visible in the authenticated dashboard. Safe modes and failures are visible, tests pass, no secrets are committed, and new adapters can reuse the same event-to-action pipeline.
