# RRPP Agent Bridge Guide

This directory is the persistent source of project context for humans and agents. Read it before changing the repository and keep it aligned with the implementation.

## Reading Order

Always read:

1. [PROJECT_BRIEF.md](PROJECT_BRIEF.md) for product intent and fixed requirements.
2. [SECURITY.md](SECURITY.md) for non-negotiable safety constraints.
3. [DECISIONS.md](DECISIONS.md) for accepted and proposed technical decisions.
4. [ERRORS.md](ERRORS.md) for relevant past mistakes and prevention rules.

Then read the documents relevant to the task:

- [ARCHITECTURE.md](ARCHITECTURE.md) for components, data flow, and invariants.
- [DELIVERY.md](DELIVERY.md) for milestones and acceptance criteria.
- [WORKFLOW.md](WORKFLOW.md) before planning, implementing, or reviewing work.
- [KNOWLEDGE.md](KNOWLEDGE.md) for verified conventions and lessons.

## Source-of-Truth Precedence

If guidance conflicts, apply this order:

1. Confirmed requirements and hard restrictions in `PROJECT_BRIEF.md`.
2. Security invariants in `SECURITY.md`.
3. Accepted decisions in `DECISIONS.md`.
4. Architecture and delivery documentation.
5. Verified entries in `KNOWLEDGE.md`.
6. Proposed decisions and assumptions.

Escalate an unresolved conflict instead of silently choosing the less restrictive rule.

## Maintenance Rules

- Give each fact one owning document and link to it elsewhere.
- Mark uncertainty as `Proposed`, `Assumption`, or `Open question`.
- Do not promote an assumption to a requirement without owner confirmation.
- Update affected documents in the same change as the behavior they describe.
- Keep historical decisions in the decision log; supersede them rather than deleting them.
- Store no secrets, credentials, personal data, raw inbound messages, or production identifiers here.

## Canonical Naming

The canonical project name is `rrpp-agent-bridge`. The current local directory may use a different spelling; directory renaming is outside this guide's scope.
