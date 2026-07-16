from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from rrpp_bridge.agent_provider import AgentContext, AgentProviderError, ConversationTurn
from rrpp_bridge.config import Settings
from rrpp_bridge.db import connect, initialize
from rrpp_bridge.executor import Executor
from rrpp_bridge.models import IntendedAction
from rrpp_bridge.openclaw_client import OpenClawAgentProvider
from rrpp_bridge.runtime import initialize_mode
from rrpp_bridge.service import ingest_local
from rrpp_bridge.workspace import add_route, create_venue


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return False

    def read(self, _limit: int) -> bytes:
        return self.payload


def openclaw_response(text: str = "Hola! Les portes obren a les 23:00.") -> bytes:
    arguments = json.dumps({
        "text": text,
        "language": "ca",
        "requires_human_review": True,
    })
    return json.dumps({
        "choices": [{"message": {"tool_calls": [{
            "function": {"name": "propose_draft", "arguments": arguments},
        }]}}],
    }).encode("utf-8")


class CapturingProvider:
    provider_id = "fake"

    def __init__(self):
        self.contexts = []

    def generate_action(self, context):
        self.contexts.append(context)
        return IntendedAction("draft_reply", {"text": "Proposta del proveïdor"})


class FailingProvider:
    provider_id = "openclaw"

    def generate_action(self, _context):
        raise AgentProviderError("openclaw_timeout")


class OpenClawClientTests(unittest.TestCase):
    def context(self) -> AgentContext:
        return AgentContext(
            correlation_id="evt-test",
            conversation_id="conv-test",
            channel="instagram",
            venue_name="Sala Test",
            venue_knowledge="Portes a les 23:00.",
            language_hint="ca",
            incoming_message="Hola, quan obriu?",
            history=(ConversationTurn("instagram", "Hola", "2026-07-16T10:00:00Z"),),
        )

    def test_calls_only_local_openclaw_and_validates_structured_draft(self):
        captured = {}

        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse(openclaw_response())

        provider = OpenClawAgentProvider(
            "http://127.0.0.1:18789", "rrpp", 7, "local-token", opener,
        )
        action = provider.generate_action(self.context())

        self.assertEqual("draft_reply", action.type)
        self.assertEqual("openclaw", action.payload["source"])
        self.assertEqual("http://127.0.0.1:18789/v1/chat/completions",
                         captured["request"].full_url)
        self.assertEqual(7, captured["timeout"])
        request_payload = json.loads(captured["request"].data)
        self.assertEqual("openclaw/rrpp", request_payload["model"])
        self.assertEqual("propose_draft", request_payload["tools"][0]["function"]["name"])
        self.assertNotIn("local-token", captured["request"].data.decode("utf-8"))

    def test_accepts_bounded_text_fallback_for_backends_without_client_tools(self):
        fallback = json.dumps({"choices": [{"message": {"content": "Resposta lliure"}}]}).encode()
        provider = OpenClawAgentProvider(
            "http://127.0.0.1:18789", "rrpp", 7, "token",
            lambda _request, timeout: FakeResponse(fallback),
        )
        action = provider.generate_action(self.context())
        self.assertEqual(("Resposta lliure", "text"),
                         (action.payload["text"], action.payload["response_format"]))

    def test_rejects_empty_text_response(self):
        invalid = json.dumps({"choices": [{"message": {"content": ""}}]}).encode()
        provider = OpenClawAgentProvider(
            "http://127.0.0.1:18789", "rrpp", 7, "token",
            lambda _request, timeout: FakeResponse(invalid),
        )
        with self.assertRaisesRegex(AgentProviderError, "openclaw_invalid_response"):
            provider.generate_action(self.context())

    def test_timeout_is_returned_as_sanitized_provider_error(self):
        def timeout(_request, timeout):
            raise TimeoutError("sensitive transport detail")

        provider = OpenClawAgentProvider(
            "http://127.0.0.1:18789", "rrpp", 7, "token", timeout,
        )
        with self.assertRaisesRegex(AgentProviderError, "^openclaw_timeout$"):
            provider.generate_action(self.context())

    def test_http_failure_is_returned_without_response_content(self):
        def http_failure(_request, timeout):
            raise HTTPError("http://127.0.0.1:18789", 503, "private detail", {}, None)

        provider = OpenClawAgentProvider(
            "http://127.0.0.1:18789", "rrpp", 7, "token", http_failure,
        )
        with self.assertRaisesRegex(AgentProviderError, "^openclaw_http_error$"):
            provider.generate_action(self.context())

    def test_enabled_config_requires_token_and_loopback_origin(self):
        valid = {
            "OPENCLAW_ENABLED": "true",
            "OPENCLAW_BASE_URL": "http://127.0.0.1:18789",
            "OPENCLAW_AGENT_ID": "rrpp",
            "OPENCLAW_TIMEOUT_SECONDS": "15",
            "OPENCLAW_GATEWAY_TOKEN": "test-only-token",
        }
        with patch("rrpp_bridge.config.load_local_env"), patch.dict(os.environ, valid, clear=True):
            settings = Settings.from_env(require_auth=False)
        self.assertTrue(settings.openclaw_enabled)
        self.assertEqual("rrpp", settings.openclaw_agent_id)
        for invalid_config in ({**valid, "OPENCLAW_BASE_URL": "https://remote.example"},
                               {**valid, "OPENCLAW_BASE_URL": "http://127.0.0.1:bad"},
                               {**valid, "OPENCLAW_GATEWAY_TOKEN": ""}):
            with patch("rrpp_bridge.config.load_local_env"), patch.dict(
                    os.environ, invalid_config, clear=True):
                with self.assertRaises(ValueError):
                    Settings.from_env(require_auth=False)


class OpenClawWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "bridge.db"
        self.conn = connect(self.path)
        initialize(self.conn)
        initialize_mode(self.conn, "shadow")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    @staticmethod
    def payload(message_id: str, body: str):
        return {"external_message_id": message_id, "sender": "ig-user",
                "recipient": "ig-business", "subject": "DM", "body_text": body}

    def test_worker_passes_venue_knowledge_history_and_creates_pending_draft(self):
        venue_id = create_venue(
            self.conn, "Sala Test", "sala-test", "ca", "test",
            "Les portes obren a les 23:00.",
        )
        add_route(self.conn, venue_id, "local", "ig-business", "test")
        ingest_local(self.conn, self.payload("m-1", "Hola"))
        Executor(self.conn).run_once("worker.first")
        ingest_local(self.conn, self.payload("m-2", "Gràcies, quan obriu?"))
        provider = CapturingProvider()

        with patch("urllib.request.urlopen") as instagram_network:
            self.assertTrue(Executor(self.conn, agent_provider=provider).run_once("worker.openclaw"))
            instagram_network.assert_not_called()

        context = provider.contexts[0]
        self.assertEqual(("Sala Test", "Les portes obren a les 23:00.", "ca"),
                         (context.venue_name, context.venue_knowledge, context.language_hint))
        self.assertEqual("Hola", context.history[-1].body_text)
        review = self.conn.execute(
            "SELECT r.kind,r.status,r.current_text,a.type FROM action_reviews r "
            "JOIN actions a ON a.id=r.action_id ORDER BY r.created_at DESC,r.id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(("draft", "pending", "Proposta del proveïdor", "draft_reply"),
                         tuple(review))
        self.assertEqual("suppressed", self.conn.execute(
            "SELECT status FROM action_executions ORDER BY created_at DESC,id DESC LIMIT 1"
        ).fetchone()[0])

    def test_provider_failure_completes_job_as_manual_escalation_without_raw_error(self):
        ingest_local(self.conn, self.payload("m-fail", "Hola"))
        self.assertTrue(Executor(self.conn, agent_provider=FailingProvider()).run_once("worker.test"))

        self.assertEqual("completed", self.conn.execute("SELECT state FROM jobs").fetchone()[0])
        self.assertEqual("processed", self.conn.execute("SELECT status FROM events").fetchone()[0])
        self.assertEqual("escalate_to_owner", self.conn.execute("SELECT type FROM actions").fetchone()[0])
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
