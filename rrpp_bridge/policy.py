from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import IntendedAction, PolicyDecision

KNOWN_ACTIONS = frozenset({
    "send_reply", "human_reply", "draft_reply", "escalate_to_owner", "no_action"
})
SAFE_REPLY_REASONS = frozenset({"catalog_answer", "greeting", "thanks", "farewell"})
REFERENCE_TYPES = frozenset({"venue", "event", "offer"})
MAX_REPLY_CHARACTERS = 1_000
MAX_REFERENCES = 10
AGENT_PAYLOAD_FIELDS = frozenset({
    "text", "language", "reason_code", "agent_action", "referenced_items",
    "structured", "source",
})

# These words identify subjects that need verified catalog support. They do not
# automatically require a human: intent and the proposed response decide that.
CONTROLLED_TOPIC_WORDS = frozenset({
    "vip", "taula", "taules", "mesa", "mesas", "reserva", "reservar", "reserves",
    "reservas", "reservacio", "reservacions", "reservation", "reservations",
    "guestlist", "llista", "llistes", "lista", "listas", "pagament", "pagaments",
    "pago", "pagos", "payment", "payments", "refund", "refunds", "reembolso",
    "reembolsos", "devolucio", "devolucions", "devolucion", "devoluciones",
    "queixa", "queixes", "queja", "quejas", "complaint", "complaints", "denuncia",
    "denuncias", "assetjament", "assetjaments", "acoso", "acosos", "harassment",
    "seguretat", "seguridad", "safety", "accident", "accidents", "dni", "passaport",
    "passaports", "pasaporte", "pasaportes", "passport", "passports", "targeta",
    "targetes", "tarjeta", "tarjetas", "card", "cards",
})
CONTROLLED_TOPIC_PHRASES = frozenset({
    "guest list", "llista de convidats", "lista de invitados", "credit card",
    "targeta de credit", "tarjeta de credito",
})
INCIDENT_WORDS = frozenset({
    "queixa", "queixes", "queja", "quejas", "complaint", "complaints", "denuncia",
    "denuncias", "assetjament", "assetjaments", "acoso", "acosos", "harassment",
    "accident", "accidente", "accidents",
})
RESTRICTED_OPERATION_PATTERNS = tuple(map(re.compile, (
    r"\breserva m\b", r"\breservame\b", r"\bbook me\b",
    r"\bmake (?:me )?a reservation\b",
    r"\bconfirma(?: m|me)? (?:la |mi |my )?(?:reserva|reservation|taula|mesa)\b",
    r"\bconfirm (?:my |the )?(?:reservation|vip table|table)\b",
    r"\bposa m (?:a|en) (?:la )?llista\b", r"\bponme en (?:la )?lista\b",
    r"\badd me to (?:the )?(?:guest list|guestlist)\b",
    r"\b(?:fes|processa|tramita)(?: me| m)? (?:un |una |el |la )?"
    r"(?:refund|reembolso|devolucio|devolucion)\b",
    r"\b(?:devuelveme|reembolsame|refund me)\b",
    r"\b(?:cobra m|cobrame|cobame|charge me|process (?:my )?payment)\b",
)))
CREDENTIAL_PATTERNS = tuple(map(re.compile, (
    r"\b(?:el meu|la meva|mi|mis|my) "
    r"(?:dni|passaport|pasaporte|passport|targeta|tarjeta|card)\b",
    r"\b(?:numero|number) (?:de |del |of (?:the )?)?"
    r"(?:targeta|tarjeta|card)\b",
    r"\b(?:cvv|pin)\b",
)))
PROHIBITED_OUTBOUND_PATTERNS = tuple(map(re.compile, (
    r"\b(?:reserva|reservation|taula|mesa) (?:esta |is |has been )?confirm",
    r"\b(?:he|hem|we have|i have) (?:fet |made |confirmat |confirmed )?"
    r"(?:la |a |the )?(?:reserva|reservation)\b",
    r"\b(?:estas|ets|you are) (?:a |en |on )?(?:la |the )?"
    r"(?:llista|lista|guest list|guestlist)\b",
    r"\b(?:pagament|pago|payment) (?:esta |is |has been )?"
    r"(?:confirmat|confirmado|confirmed|processat|procesado|processed|fet|hecho|done)\b",
    r"\b(?:refund|reembolso|devolucio|devolucion) (?:esta |is |has been )?"
    r"(?:confirmat|confirmado|confirmed|processat|procesado|processed|issued|fet|hecho|done)\b",
    r"\b(?:envia|enviam|facilita|dona|send|provide)(?: me| m)?"
    r"(?: (?:el|la|els|les|un|una|teu|teva|meu|meva|tu|tus|mi|mis|your|the))* "
    r"(?:dni|passaport|pasaporte|passport|targeta|tarjeta|card|cvv|pin)\b",
)))


def _normalized_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    without_marks = "".join(
        character for character in folded if unicodedata.category(character) != "Mn"
    )
    return " ".join(re.findall(r"[a-z0-9]+", without_marks))


def _contains_phrase(normalized: str, phrases: frozenset[str]) -> bool:
    padded = f" {normalized} "
    return any(f" {phrase} " in padded for phrase in phrases)


def controlled_topic_present(text: str) -> bool:
    if not isinstance(text, str):
        return True
    normalized = _normalized_text(text)
    words = set(normalized.split())
    return bool(words & CONTROLLED_TOPIC_WORDS) or _contains_phrase(
        normalized, CONTROLLED_TOPIC_PHRASES
    )


def human_review_required(text: str) -> bool:
    """Return true for incidents or supplied credentials, not general FAQs."""
    if not isinstance(text, str):
        return True
    normalized = _normalized_text(text)
    words = set(normalized.split())
    if words & INCIDENT_WORDS or any(pattern.search(normalized) for pattern in CREDENTIAL_PATTERNS):
        return True
    if words & {"targeta", "tarjeta", "card", "cards"}:
        digits = re.sub(r"\D", "", text)
        if 12 <= len(digits) <= 19:
            return True
    return False


def restricted_operation_requested(text: str) -> bool:
    if not isinstance(text, str):
        return True
    normalized = _normalized_text(text)
    return any(pattern.search(normalized) for pattern in RESTRICTED_OPERATION_PATTERNS)


def prohibited_outbound_claim(text: str) -> bool:
    if not isinstance(text, str):
        return True
    normalized = _normalized_text(text)
    return any(pattern.search(normalized) for pattern in PROHIBITED_OUTBOUND_PATTERNS)


def _valid_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > MAX_REPLY_CHARACTERS or "\x00" in text:
        return False
    return not any(ord(character) < 32 and character not in "\r\n\t" for character in text)


def _valid_language(value: Any) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?", value)
    )


def _valid_source(value: Any) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value)
    )


def _valid_references(value: Any) -> bool:
    if not isinstance(value, list) or len(value) > MAX_REFERENCES:
        return False
    seen: set[tuple[str, str, str]] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"type", "id", "verified_at"}:
            return False
        item_type, item_id, verified_at = (
            item["type"], item["id"], item["verified_at"]
        )
        if (item_type not in REFERENCE_TYPES
                or not isinstance(item_id, str) or item_id != item_id.strip()
                or not 1 <= len(item_id) <= 100
                or not isinstance(verified_at, str) or verified_at != verified_at.strip()
                or not 1 <= len(verified_at) <= 100):
            return False
        key = (item_type, item_id, verified_at)
        if key in seen:
            return False
        seen.add(key)
    return True


def _valid_agent_payload(payload: dict[str, Any]) -> bool:
    return (
        set(payload) == AGENT_PAYLOAD_FIELDS
        and _valid_text(payload.get("text"))
        and _valid_language(payload.get("language"))
        and _valid_source(payload.get("source"))
        and _valid_references(payload.get("referenced_items"))
    )


class Policy:
    """Deterministic bridge policy. Model claims never broaden delivery permission."""

    def decide(self, action: IntendedAction, *, channel: str = "local",
               incoming_text: str = "", bot_paused: bool = False) -> PolicyDecision:
        if action.type not in KNOWN_ACTIONS:
            return PolicyDecision("blocked", "policy.unknown-action.v3",
                                  "Action type has no explicit policy coverage")
        if action.type == "escalate_to_owner":
            return PolicyDecision("escalated", "policy.escalation.v3",
                                  "Request requires a human")
        if action.type == "draft_reply":
            return PolicyDecision("pending_approval", "policy.compatibility-review.v3",
                                  "Legacy or unstructured output requires review")
        if action.type == "human_reply":
            if channel != "instagram":
                return PolicyDecision("blocked", "policy.human-channel.v3",
                                      "Human delivery is not implemented for this channel")
            if set(action.payload) != {"text"} or not _valid_text(action.payload.get("text")):
                return PolicyDecision("blocked", "policy.human-payload.v3",
                                      "Human delivery payload is invalid")
            return PolicyDecision("allowed", "policy.authenticated-human.v3",
                                  "Authenticated human response may use the delivery queue")
        if bot_paused:
            return PolicyDecision("escalated", "policy.conversation-paused.v3",
                                  "Bot is paused for this conversation")
        if human_review_required(incoming_text):
            return PolicyDecision("escalated", "policy.incident-or-credential.v3",
                                  "Incident, complaint, or supplied credential requires a human")
        if restricted_operation_requested(incoming_text):
            return PolicyDecision("escalated", "policy.restricted-operation.v3",
                                  "Customer-specific business operation requires a human")
        if action.type == "no_action":
            payload = action.payload
            if controlled_topic_present(incoming_text):
                return PolicyDecision("escalated", "policy.ignore-guard.v3",
                                      "A controlled-topic message cannot be silently ignored")
            if payload.get("structured") is not True:
                return PolicyDecision("pending_approval", "policy.structured-output.v3",
                                      "Unstructured model output cannot be ignored automatically")
            if (set(payload) != AGENT_PAYLOAD_FIELDS
                    or payload.get("agent_action") != "ignore"
                    or payload.get("reason_code") != "spam_or_non_message"
                    or not (payload.get("text") is None or payload.get("text") == "")
                    or not _valid_language(payload.get("language"))
                    or not _valid_source(payload.get("source"))
                    or not _valid_references(payload.get("referenced_items"))
                    or payload.get("referenced_items")):
                return PolicyDecision("escalated", "policy.ignore-guard.v3",
                                      "Only a complete structured spam decision may be ignored")
            return PolicyDecision("ignored", "policy.no-action.v3",
                                  "Structured non-message or spam requires no response")
        if channel != "instagram":
            return PolicyDecision("pending_approval", "policy.channel-review.v3",
                                  "Automatic delivery is enabled only for Instagram")
        payload = action.payload
        if payload.get("structured") is not True:
            return PolicyDecision("pending_approval", "policy.structured-output.v3",
                                  "Unstructured model output cannot be sent automatically")
        if not _valid_agent_payload(payload):
            return PolicyDecision("escalated", "policy.invalid-payload.v3",
                                  "Automatic delivery payload is malformed or incomplete")
        reply_text = str(payload["text"]).strip()
        if prohibited_outbound_claim(reply_text):
            return PolicyDecision("escalated", "policy.prohibited-claim.v3",
                                  "Proposed response claims or requests a prohibited operation")
        decision_action = str(payload["agent_action"])
        reason_code = str(payload["reason_code"])
        references = payload["referenced_items"]
        if decision_action == "ask_clarification" and reason_code == "missing_details":
            if references:
                return PolicyDecision("escalated", "policy.reference-mismatch.v3",
                                      "Clarifications cannot claim catalog support")
            return PolicyDecision("allowed", "policy.safe-clarification.v3",
                                  "A bounded clarification contains no unsupported decision")
        if decision_action != "reply" or reason_code not in SAFE_REPLY_REASONS:
            return PolicyDecision("escalated", "policy.unsupported-decision.v3",
                                  "Decision is not eligible for automatic delivery")
        controlled_topic = (
            controlled_topic_present(incoming_text) or controlled_topic_present(reply_text)
        )
        if controlled_topic and reason_code != "catalog_answer":
            return PolicyDecision("escalated", "policy.controlled-topic-reference.v3",
                                  "Controlled-topic replies require verified catalog support")
        if reason_code == "catalog_answer":
            if not references:
                return PolicyDecision("escalated", "policy.catalog-reference.v3",
                                      "Catalog answers require verified catalog references")
        elif references:
            return PolicyDecision("escalated", "policy.reference-mismatch.v3",
                                  "Low-risk conversational replies cannot claim catalog support")
        return PolicyDecision("allowed", "policy.safe-instagram-reply.v3",
                              "Structured reply passed deterministic safety checks")
