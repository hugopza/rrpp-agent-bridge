from __future__ import annotations

import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from rrpp_bridge.agent_provider import AgentContext, AgentProviderError, ConversationTurn
from rrpp_bridge.config import Settings
from rrpp_bridge.db import connect, initialize
from rrpp_bridge.executor import Executor
from rrpp_bridge.models import AgentDecision, CatalogItem
from rrpp_bridge.openclaw_client import OpenClawAgentProvider
from rrpp_bridge.runtime import initialize_mode
from rrpp_bridge.service import ingest_local
from rrpp_bridge.workspace import create_venue


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int):
        return self.body


def response_with_message(message: dict) -> bytes:
    return json.dumps({"choices": [{"message": message}]}).encode()


def structured_response() -> bytes:
    arguments = {
        "action": "reply", "text": "Hola! Obrim a les 23:00.", "language": "ca",
        "reason_code": "catalog_answer",
        "referenced_items": [{"type": "venue", "id": "ven-1", "verified_at": "verified-1"}],
    }
    return response_with_message({
        "tool_calls": [{"function": {"name": "submit_decision",
                                      "arguments": json.dumps(arguments)}}]
    })


def context() -> AgentContext:
    return AgentContext(
        correlation_id="evt-1", conversation_id="conv-1", channel="instagram",
        receiver_account_id="ig-business", external_user_id="ig-user",
        language_hint="ca", incoming_message="Quan obriu?",
        history=(ConversationTurn("inbound", "customer", "Hola", "2026-01-01T00:00:00+00:00"),),
        catalog_items=(CatalogItem("venue", "ven-1", "verified-1",
                                   {"name": "Sala", "verified_notes": "Obrim a les 23:00"}),),
        bot_paused=False,
    )


class OpenClawClientTests(unittest.TestCase):
    def provider(self, opener):
        return OpenClawAgentProvider(
            "http://127.0.0.1:18789", "rrpp", 5, "gateway-secret", opener=opener
        )

    def test_validates_structured_decision_and_local_request(self):
        captured = {}

        def opener(request, timeout):
            captured["request"], captured["timeout"] = request, timeout
            return FakeResponse(structured_response())

        decision = self.provider(opener).generate_decision(context())
        self.assertEqual(("reply", "catalog_answer", True),
                         (decision.action, decision.reason_code, decision.structured))
        payload = json.loads(captured["request"].data)
        self.assertEqual("openclaw/rrpp", payload["model"])
        self.assertEqual("rrpp-bridge:v2:conv-1", payload["user"])
        self.assertEqual("Bearer gateway-secret",
                         captured["request"].headers["Authorization"])
        self.assertEqual("submit_decision", payload["tools"][0]["function"]["name"])
        self.assertNotIn("gateway-secret", captured["request"].data.decode())

    def test_accepts_json_text_and_keeps_plain_text_human_only(self):
        json_body = response_with_message({"content": json.dumps({
            "action": "ask_clarification", "text": "Per a quina nit?", "language": "ca",
            "reason_code": "missing_details", "referenced_items": [],
        })})
        decision = self.provider(lambda *_args, **_kwargs: FakeResponse(json_body)).generate_decision(context())
        self.assertEqual(("ask_clarification", True), (decision.action, decision.structured))
        plain = response_with_message({"content": "Et preparem una resposta."})
        fallback = self.provider(lambda *_args, **_kwargs: FakeResponse(plain)).generate_decision(context())
        self.assertEqual(("human_required", False), (fallback.action, fallback.structured))

    def test_rejects_unknown_catalog_reference(self):
        body = structured_response().replace(b"ven-1", b"invented")
        with self.assertRaisesRegex(AgentProviderError, "openclaw_invalid_response"):
            self.provider(lambda *_args, **_kwargs: FakeResponse(body)).generate_decision(context())

    def test_timeout_and_http_errors_are_sanitized(self):
        def timeout(*_args, **_kwargs):
            raise socket.timeout("secret transport detail")

        with self.assertRaisesRegex(AgentProviderError, "^openclaw_timeout$"):
            self.provider(timeout).generate_decision(context())

        def http(request, *_args, **_kwargs):
            raise HTTPError(request.full_url, 500, "private body", {}, io.BytesIO(b"secret"))

        with self.assertRaisesRegex(AgentProviderError, "^openclaw_http_error$"):
            self.provider(http).generate_decision(context())

    def test_enabled_config_requires_token_and_loopback(self):
        env = {
            "RRPP_DATABASE_PATH": "var/test-openclaw.db", "RRPP_MODE": "shadow",
            "OPENCLAW_ENABLED": "true", "OPENCLAW_GATEWAY_TOKEN": "secret",
            "OPENCLAW_BASE_URL": "http://127.0.0.1:18789", "OPENCLAW_AGENT_NAME": "rrpp",
        }
        with patch.dict("os.environ", env, clear=True), patch("rrpp_bridge.config.load_local_env"):
            self.assertTrue(Settings.from_env(require_auth=False).openclaw_enabled)
        env["OPENCLAW_BASE_URL"] = "https://remote.example"
        with patch.dict("os.environ", env, clear=True), patch("rrpp_bridge.config.load_local_env"):
            with self.assertRaisesRegex(ValueError, "loopback"):
                Settings.from_env(require_auth=False)


class FailingProvider:
    provider_id = "openclaw"

    def generate_decision(self, _context):
        raise AgentProviderError("openclaw_timeout")


class CapturingProvider:
    provider_id = "fake-openclaw"

    def __init__(self):
        self.contexts = []

    def generate_decision(self, value):
        self.contexts.append(value)
        return AgentDecision("reply", "Hola!", "ca", "greeting", structured=True)


class OpenClawWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "worker.db")
        initialize(self.conn)
        initialize_mode(self.conn, "shadow")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    @staticmethod
    def payload(message_id: str, body: str):
        return {"external_message_id": message_id, "sender": "ig-user",
                "recipient": "ig-business", "subject": "DM", "body_text": body}

    def test_worker_passes_catalog_and_history_then_creates_read_only_draft(self):
        create_venue(self.conn, "Sala Test", "sala-test", "ca", "test", "Obrim a les 23:00")
        ingest_local(self.conn, self.payload("m-1", "Hola"))
        Executor(self.conn).run_once("worker.first")
        ingest_local(self.conn, self.payload("m-2", "Gracies"))
        provider = CapturingProvider()
        self.assertTrue(Executor(self.conn, agent_provider=provider).run_once("worker.openclaw"))
        value = provider.contexts[0]
        self.assertEqual("Hola", value.history[-1].body_text)
        self.assertEqual("Sala Test", value.catalog_items[0].data["name"])
        review = self.conn.execute(
            "SELECT r.kind,r.status,r.current_text,a.type FROM action_reviews r "
            "JOIN actions a ON a.id=r.action_id ORDER BY r.rowid DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(("draft", "pending", "Hola!", "send_reply"), tuple(review))

    def test_provider_failure_is_visible_manual_escalation(self):
        ingest_local(self.conn, self.payload("m-fail", "Hola"))
        self.assertTrue(Executor(self.conn, agent_provider=FailingProvider()).run_once("worker.test"))
        self.assertEqual(("escalation", "pending"), tuple(self.conn.execute(
            "SELECT kind,status FROM action_reviews"
        ).fetchone()))
        audit = " ".join(row[0] for row in self.conn.execute(
            "SELECT details_json FROM audit_log WHERE operation='agent.generation_failed'"
        ))
        self.assertIn("openclaw_timeout", audit)
        self.assertNotIn("transport", audit)


if __name__ == "__main__":
    unittest.main()
