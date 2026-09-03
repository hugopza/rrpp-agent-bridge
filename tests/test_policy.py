from __future__ import annotations

import unittest

from rrpp_bridge.models import IntendedAction
from rrpp_bridge.policy import (Policy, controlled_topic_present, human_review_required,
                                prohibited_outbound_claim, restricted_operation_requested)


def agent_payload(*, action: str = "reply", text: object = "Hola!",
                  reason: str = "greeting", structured: object = True,
                  references: object = None, **extra) -> dict:
    payload = {
        "text": text,
        "language": "ca",
        "reason_code": reason,
        "agent_action": action,
        "referenced_items": [] if references is None else references,
        "structured": structured,
        "source": "test-provider",
    }
    payload.update(extra)
    return payload


def reference() -> dict[str, str]:
    return {"type": "event", "id": "event-1", "verified_at": "2026-08-11"}


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy()

    def decide(self, payload: dict, *, action_type: str = "send_reply",
               incoming: str = "Hola", channel: str = "instagram",
               paused: bool = False):
        return self.policy.decide(
            IntendedAction(action_type, payload), channel=channel,
            incoming_text=incoming, bot_paused=paused,
        )

    def test_topic_words_are_not_human_review_by_themselves(self):
        for value in (
            "Com funciona una reserva VIP?", "Quins mètodes de pagament accepteu?",
            "Quina és la política de devolucions?", "Cal portar DNI?", "On és seguretat?",
        ):
            self.assertTrue(controlled_topic_present(value), value)
            self.assertFalse(human_review_required(value), value)

    def test_word_matching_avoids_substring_false_positives(self):
        for value in (
            "Apago el llum", "Treballa una especialista", "Aquesta és una promesa",
        ):
            self.assertFalse(controlled_topic_present(value), value)
            self.assertFalse(human_review_required(value), value)

    def test_verified_informational_answers_for_controlled_topics_are_allowed(self):
        cases = (
            ("Com funciona una reserva VIP?", "La reserva es fa al web oficial."),
            ("Quins mètodes de pagament accepteu?", "El pagament es fa al web oficial."),
            ("Quina és la política de devolucions?", "La política consta al web oficial."),
            ("Cal portar DNI?", "Cal portar el document indicat a les condicions."),
            ("On és seguretat?", "El punt de seguretat és a l'entrada principal."),
        )
        for incoming, outgoing in cases:
            decision = self.decide(agent_payload(
                text=outgoing, reason="catalog_answer", references=[reference()]
            ), incoming=incoming)
            self.assertEqual("allowed", decision.outcome, incoming)

    def test_controlled_topic_without_catalog_support_is_escalated(self):
        greeting = self.decide(agent_payload(), incoming="Com funciona una reserva?")
        no_reference = self.decide(agent_payload(
            text="La reserva es fa al web.", reason="catalog_answer"
        ), incoming="Com funciona una reserva?")
        self.assertEqual("policy.controlled-topic-reference.v3", greeting.policy_id)
        self.assertEqual("policy.catalog-reference.v3", no_reference.policy_id)

    def test_customer_specific_operations_still_require_a_human(self):
        for value in (
            "Reserva'm una taula", "Resérvame una mesa", "Book me a VIP table",
            "Posa'm a la llista", "Ponme en la lista", "Refund me",
            "Processa'm una devolució", "Cóbame el pago",
        ):
            self.assertTrue(restricted_operation_requested(value), value)
            decision = self.decide(agent_payload(
                text="Consulta el procediment.", reason="catalog_answer",
                references=[reference()]
            ), incoming=value)
            self.assertEqual("policy.restricted-operation.v3", decision.policy_id, value)

    def test_incidents_and_supplied_credentials_require_a_human(self):
        for value in (
            "Tinc una queixa", "Quiero presentar una denuncia", "He patit assetjament",
            "Ha habido un accidente", "El meu DNI és 12345678Z", "Mi tarjeta es 4242424242424242",
        ):
            self.assertTrue(human_review_required(value), value)
            decision = self.decide(agent_payload(), incoming=value)
            self.assertEqual("policy.incident-or-credential.v3", decision.policy_id, value)

    def test_prohibited_outbound_claims_never_send(self):
        for value in (
            "La reserva està confirmada", "Estás en la lista", "Payment processed",
            "Refund issued", "Envia'm el teu DNI",
        ):
            self.assertTrue(prohibited_outbound_claim(value), value)
            decision = self.decide(agent_payload(
                text=value, reason="catalog_answer", references=[reference()]
            ))
            self.assertEqual("policy.prohibited-claim.v3", decision.policy_id, value)

    def test_safe_structured_reply_and_clarification_are_allowed(self):
        reply = self.decide(agent_payload())
        clarification = self.decide(agent_payload(
            action="ask_clarification", text="Per a quina nit?", reason="missing_details"
        ), incoming="Com puc reservar?")
        self.assertEqual(("allowed", "policy.safe-instagram-reply.v3"),
                         (reply.outcome, reply.policy_id))
        self.assertEqual(("allowed", "policy.safe-clarification.v3"),
                         (clarification.outcome, clarification.policy_id))

    def test_unstructured_or_inconsistent_ignore_is_never_silent(self):
        unstructured = self.decide(agent_payload(
            action="ignore", text="", reason="spam_or_non_message", structured=False
        ), action_type="no_action")
        mismatched = self.decide(agent_payload(
            action="reply", text="", reason="spam_or_non_message"
        ), action_type="no_action")
        valid = self.decide(agent_payload(
            action="ignore", text="", reason="spam_or_non_message"
        ), action_type="no_action")
        topic = self.decide(agent_payload(
            action="ignore", text="", reason="spam_or_non_message"
        ), action_type="no_action", incoming="Informació de reserva")
        self.assertEqual("pending_approval", unstructured.outcome)
        self.assertEqual("escalated", mismatched.outcome)
        self.assertEqual(("ignored", "policy.no-action.v3"),
                         (valid.outcome, valid.policy_id))
        self.assertEqual("policy.ignore-guard.v3", topic.policy_id)

    def test_malformed_payloads_and_references_fail_closed(self):
        item = reference()
        cases = (
            agent_payload(text=""), agent_payload(text="Hola\x01"),
            agent_payload(unexpected="field"),
            agent_payload(reason="catalog_answer", references=[item, item]),
            agent_payload(reason="catalog_answer", references=[{
                "type": "unknown", "id": "value", "verified_at": "now"
            }]),
        )
        for payload in cases:
            self.assertNotEqual("allowed", self.decide(payload).outcome)

    def test_pause_channel_human_and_unknown_action_boundaries(self):
        paused = self.decide(agent_payload(), paused=True)
        local = self.decide(agent_payload(), channel="local")
        human = self.decide({"text": "Resposta humana"}, action_type="human_reply")
        bad_human = self.decide({"text": ""}, action_type="human_reply")
        unknown = self.decide({}, action_type="send_money")
        self.assertEqual("policy.conversation-paused.v3", paused.policy_id)
        self.assertEqual("pending_approval", local.outcome)
        self.assertEqual("allowed", human.outcome)
        self.assertEqual("blocked", bad_human.outcome)
        self.assertEqual("policy.unknown-action.v3", unknown.policy_id)


if __name__ == "__main__":
    unittest.main()
