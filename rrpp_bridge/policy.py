from __future__ import annotations

from .models import IntendedAction, PolicyDecision

KNOWN_ACTIONS = frozenset({"draft_reply", "escalate_to_owner", "no_action"})


class Policy:
    """Conservative V1 policy. Unknown actions fail closed."""

    def decide(self, action: IntendedAction) -> PolicyDecision:
        if action.type not in KNOWN_ACTIONS:
            return PolicyDecision("blocked", "policy.unknown-action.v1",
                                  "Action type has no explicit policy coverage")
        if action.type == "escalate_to_owner":
            return PolicyDecision("escalated", "policy.escalation.v1",
                                  "Sensitive request requires owner review")
        if action.type == "no_action":
            return PolicyDecision("ignored", "policy.no-action.v1", "No response is required")
        return PolicyDecision("pending_approval", "policy.human-review.v1",
                              "Draft requires explicit human review and cannot be sent")
