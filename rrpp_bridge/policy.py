from __future__ import annotations

from .models import IntendedAction, PolicyDecision

KNOWN_ACTIONS = frozenset({
    "send_reply", "human_reply", "draft_reply", "escalate_to_owner", "no_action"
})
SAFE_REPLY_REASONS = frozenset({"catalog_answer", "greeting", "thanks", "farewell"})
HARD_REVIEW_TERMS = (
    "vip", "taula", "mesa", "reserva", "reservation", "guest list", "guestlist",
    "llista", "lista", "pagament", "pago", "payment", "refund", "devoluci",
    "queixa", "queja", "complaint", "denuncia", "assetj", "acoso", "seguretat",
    "seguridad", "accident", "dni", "passaport", "pasaporte", "targeta", "tarjeta",
)


def hard_review_required(text: str) -> bool:
    normalized = text.casefold()
    return any(term in normalized for term in HARD_REVIEW_TERMS)


class Policy:
    """Deterministic bridge policy. The model cannot broaden execution permission."""

    def decide(self, action: IntendedAction, *, channel: str = "local",
               incoming_text: str = "", bot_paused: bool = False) -> PolicyDecision:
        if action.type not in KNOWN_ACTIONS:
            return PolicyDecision("blocked", "policy.unknown-action.v2",
                                  "Action type has no explicit policy coverage")
        if action.type == "escalate_to_owner":
            return PolicyDecision("escalated", "policy.escalation.v2",
                                  "Request requires a human")
        if action.type == "no_action":
            reason = str(action.payload.get("reason_code", ""))
            if reason != "spam_or_non_message":
                return PolicyDecision("escalated", "policy.ignore-guard.v2",
                                      "A customer message cannot be silently ignored")
            return PolicyDecision("ignored", "policy.no-action.v2", "No response is justified")
        if action.type == "draft_reply":
            return PolicyDecision("pending_approval", "policy.compatibility-review.v2",
                                  "Legacy or unstructured output requires review")
        if action.type == "human_reply":
            if channel != "instagram":
                return PolicyDecision("blocked", "policy.human-channel.v2",
                                      "Human delivery is not implemented for this channel")
            return PolicyDecision("allowed", "policy.authenticated-human.v2",
                                  "Authenticated human response may use the delivery queue")
        if bot_paused:
            return PolicyDecision("escalated", "policy.conversation-paused.v2",
                                  "Bot is paused for this conversation")
        if channel != "instagram":
            return PolicyDecision("pending_approval", "policy.channel-review.v2",
                                  "Automatic delivery is enabled only for Instagram")
        if hard_review_required(incoming_text):
            return PolicyDecision("escalated", "policy.hard-review.v2",
                                  "Sensitive or business-critical request requires a human")
        if action.payload.get("structured") is not True:
            return PolicyDecision("pending_approval", "policy.structured-output.v2",
                                  "Unstructured model output cannot be sent automatically")
        decision_action = str(action.payload.get("agent_action", ""))
        reason_code = str(action.payload.get("reason_code", ""))
        references = action.payload.get("referenced_items")
        if decision_action == "ask_clarification" and reason_code == "missing_details":
            return PolicyDecision("allowed", "policy.safe-clarification.v2",
                                  "A bounded clarification contains no unsupported decision")
        if decision_action != "reply" or reason_code not in SAFE_REPLY_REASONS:
            return PolicyDecision("escalated", "policy.unsupported-decision.v2",
                                  "Decision is not eligible for automatic delivery")
        if reason_code == "catalog_answer" and not references:
            return PolicyDecision("escalated", "policy.catalog-reference.v2",
                                  "Commercial answers require verified catalog references")
        return PolicyDecision("allowed", "policy.safe-instagram-reply.v2",
                              "Structured reply passed deterministic safety checks")
