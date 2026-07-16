from __future__ import annotations

import tempfile
import unittest
import json
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

from rrpp_bridge.db import connect, initialize
from rrpp_bridge.delivery import create_human_reply
from rrpp_bridge.executor import Executor
from rrpp_bridge.instagram_sender import (InstagramSendError, InstagramSendResult,
                                           InstagramSender)
from rrpp_bridge.models import AgentDecision, NormalizedEvent
from rrpp_bridge.queue import JobQueue
from rrpp_bridge.runtime import initialize_mode, set_mode
from rrpp_bridge.workspace import set_bot_paused


class SafeProvider:
    provider_id = "fake-openclaw"

    def generate_decision(self, _context):
        return AgentDecision("reply", "Hola! Com et podem ajudar?", "ca", "greeting")


class FakeSender:
    def __init__(self, error: InstagramSendError | None = None):
        self.calls = []
        self.error = error

    def send_text(self, recipient_id, text):
        self.calls.append((recipient_id, text))
        if self.error:
            raise self.error
        return InstagramSendResult(recipient_id, "meta-message-1")


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return json.dumps(self.payload).encode("utf-8")


class InstagramSenderTests(unittest.TestCase):
    def test_official_request_contract_uses_bearer_token(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data)
            captured["timeout"] = timeout
            return FakeResponse({"recipient_id": "ig-user", "message_id": "ig-message"})

        sender = InstagramSender(
            "https://graph.instagram.com", "v24.0", "ig-business", "secret-token", 7,
            opener=opener,
        )
        result = sender.send_text("ig-user", "Hola")
        self.assertEqual("https://graph.instagram.com/v24.0/ig-business/messages", captured["url"])
        self.assertEqual("Bearer secret-token", captured["headers"]["Authorization"])
        self.assertEqual({"recipient": {"id": "ig-user"}, "message": {"text": "Hola"}},
                         captured["body"])
        self.assertEqual(("ig-user", "ig-message"), (result.recipient_id, result.message_id))

    def test_rejection_is_definite_but_server_failure_is_ambiguous(self):
        for status, ambiguous in ((400, False), (500, True)):
            def opener(request, timeout, status=status):
                raise HTTPError(request.full_url, status, "failed", {}, BytesIO(b"{}"))

            sender = InstagramSender(
                "https://graph.instagram.com", "v24.0", "ig-business", "secret", 7,
                opener=opener,
            )
            with self.assertRaises(InstagramSendError) as caught:
                sender.send_text("ig-user", "Hola")
            self.assertEqual(ambiguous, caught.exception.ambiguous)


class DeliveryFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "delivery.db")
        initialize(self.conn)
        initialize_mode(self.conn, "shadow")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def enqueue(self, message_id="ig-1", text="Hola", sender="ig-user"):
        return JobQueue(self.conn).enqueue(NormalizedEvent(
            channel="instagram", external_message_id=message_id, sender=sender,
            recipient="ig-business", subject="Instagram DM", body_text=text,
            work_key=f"instagram:ig-business:{sender}",
        ))

    def test_live_dm_is_generated_delivered_and_visible_in_history(self):
        self.enqueue()
        set_mode(self.conn, "live", "test")
        sender = FakeSender()
        worker = Executor(self.conn, agent_provider=SafeProvider(), instagram_sender=sender)
        self.assertTrue(worker.run_once("worker.test"))
        self.assertEqual("pending", self.conn.execute("SELECT status FROM deliveries").fetchone()[0])
        self.assertTrue(worker.run_once("worker.test"))
        self.assertEqual([("ig-user", "Hola! Com et podem ajudar?")], sender.calls)
        self.assertEqual(("sent", "meta-message-1"), tuple(self.conn.execute(
            "SELECT status,external_message_id FROM deliveries"
        ).fetchone()))
        self.assertEqual(("outbound", "bot", "sent"), tuple(self.conn.execute(
            "SELECT direction,author_type,status FROM conversation_messages "
            "WHERE direction='outbound'"
        ).fetchone()))
        self.assertEqual("resolved", self.conn.execute(
            "SELECT status FROM conversations"
        ).fetchone()[0])

    def test_shadow_keeps_safe_reply_as_draft_without_sender_call(self):
        self.enqueue()
        sender = FakeSender()
        Executor(self.conn, agent_provider=SafeProvider(), instagram_sender=sender).run_once("worker")
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM deliveries").fetchone()[0])
        self.assertEqual("pending", self.conn.execute("SELECT status FROM action_reviews").fetchone()[0])
        self.assertEqual([], sender.calls)

    def test_sensitive_request_and_paused_conversation_never_auto_send(self):
        self.enqueue(text="Em reserves una taula VIP?")
        set_mode(self.conn, "live", "test")
        sender = FakeSender()
        Executor(self.conn, agent_provider=SafeProvider(), instagram_sender=sender).run_once("worker")
        self.assertEqual("escalated", self.conn.execute(
            "SELECT outcome FROM policy_decisions"
        ).fetchone()[0])
        self.assertEqual([], sender.calls)

        self.enqueue("ig-2", "Hola de nou")
        conversation_id = self.conn.execute("SELECT id FROM conversations").fetchone()[0]
        set_bot_paused(self.conn, conversation_id, True, "test", "manual")
        Executor(self.conn, agent_provider=SafeProvider(), instagram_sender=sender).run_once("worker")
        self.assertEqual([], sender.calls)

    def test_ambiguous_delivery_pauses_bot_for_reconciliation(self):
        self.enqueue()
        set_mode(self.conn, "live", "test")
        sender = FakeSender(InstagramSendError("instagram_delivery_unknown", ambiguous=True))
        worker = Executor(self.conn, agent_provider=SafeProvider(), instagram_sender=sender)
        worker.run_once("worker")
        worker.run_once("worker")
        self.assertEqual("unknown", self.conn.execute("SELECT status FROM deliveries").fetchone()[0])
        self.assertEqual((1, "pending_review"), tuple(self.conn.execute(
            "SELECT bot_paused,status FROM conversations"
        ).fetchone()))

    def test_human_reply_uses_same_delivery_queue(self):
        self.enqueue()
        conversation_id = self.conn.execute("SELECT id FROM conversations").fetchone()[0]
        set_mode(self.conn, "live", "test")
        delivery_id = create_human_reply(self.conn, conversation_id, "Resposta humana", "dashboard:admin")
        sender = FakeSender()
        Executor(self.conn, agent_provider=SafeProvider(), instagram_sender=sender).run_once("worker")
        self.assertEqual("sent", self.conn.execute(
            "SELECT status FROM deliveries WHERE id=?", (delivery_id,)
        ).fetchone()[0])
        self.assertEqual("human", self.conn.execute(
            "SELECT author_type FROM conversation_messages WHERE direction='outbound'"
        ).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
