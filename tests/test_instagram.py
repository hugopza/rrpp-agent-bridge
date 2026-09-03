from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from rrpp_bridge.adapters.instagram import normalize
from rrpp_bridge.config import InstagramAccountSettings, Settings, load_local_env
from rrpp_bridge.db import connect, initialize
from rrpp_bridge.executor import Executor
from rrpp_bridge.instagram_webhook import InstagramWebhookApplication
from rrpp_bridge.models import IntendedAction
from rrpp_bridge.runtime import initialize_mode, set_mode
from rrpp_bridge.workspace import add_route, create_venue


def payload(message_id: str = "ig-mid-1", text: str = "Hola, teniu entrades?",
            sender: str = "ig-user-1", recipient: str = "ig-business-1") -> dict:
    return {
        "object": "instagram",
        "entry": [{
            "id": recipient,
            "time": 1_751_000_000,
            "messaging": [{
                "sender": {"id": sender},
                "recipient": {"id": recipient},
                "timestamp": 1_751_000_000_000,
                "message": {"mid": message_id, "text": text},
            }],
        }],
    }


class InstagramWebhookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "instagram.db"
        conn = connect(self.path)
        initialize(conn)
        initialize_mode(conn, "shadow")
        conn.close()
        self.settings = Settings(
            database_path=self.path, mode="shadow", dashboard_user="", dashboard_password="",
            session_secret="", instagram_enabled=True, instagram_verify_token="verify-me",
            instagram_app_secret="app-secret", instagram_business_account_id="ig-send-account-1",
            instagram_webhook_account_id="ig-business-1",
        )
        self.app = InstagramWebhookApplication(self.settings)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, method: str, *, query: str = "", body: bytes = b"",
                signature: str | None = None, content_type: str = "application/json",
                app: InstagramWebhookApplication | None = None):
        captured: dict[str, object] = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = headers

        environ = {
            "REQUEST_METHOD": method, "PATH_INFO": "/webhooks/instagram",
            "QUERY_STRING": query, "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
        }
        if signature is not None:
            environ["HTTP_X_HUB_SIGNATURE_256"] = signature
        response = b"".join((app or self.app)(environ, start_response))
        return str(captured["status"]), response

    def signed_request(self, data: dict, app: InstagramWebhookApplication | None = None):
        body = json.dumps(data, separators=(",", ":")).encode()
        digest = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
        return self.request("POST", body=body, signature=f"sha256={digest}", app=app)

    def test_get_verification_accepts_only_matching_token_and_subscribe_mode(self):
        query = urlencode({"hub.mode": "subscribe", "hub.verify_token": "verify-me",
                           "hub.challenge": "challenge-123"})
        self.assertEqual(("200 OK", b"challenge-123"), self.request("GET", query=query))
        bad = urlencode({"hub.mode": "subscribe", "hub.verify_token": "wrong",
                         "hub.challenge": "challenge-123"})
        self.assertEqual("403 Forbidden", self.request("GET", query=bad)[0])
        wrong_mode = urlencode({"hub.mode": "other", "hub.verify_token": "verify-me"})
        self.assertEqual("403 Forbidden", self.request("GET", query=wrong_mode)[0])

    def test_disabled_connector_is_not_exposed(self):
        disabled = InstagramWebhookApplication(
            Settings(database_path=self.path, mode="shadow", dashboard_user="",
                     dashboard_password="", session_secret="")
        )
        status: list[str] = []
        result = disabled({"PATH_INFO": "/webhooks/instagram", "REQUEST_METHOD": "GET"},
                          lambda value, _headers: status.append(value))
        self.assertEqual(("404 Not Found", b"Not found"), (status[0], b"".join(result)))

    def test_healthcheck_is_private_route_ready_and_checks_configuration(self):
        status: list[str] = []
        result = self.app(
            {"PATH_INFO": "/healthz", "REQUEST_METHOD": "GET"},
            lambda value, _headers: status.append(value),
        )
        self.assertEqual(("200 OK", b"OK"), (status[0], b"".join(result)))

        disabled = InstagramWebhookApplication(
            Settings(database_path=self.path, mode="shadow", dashboard_user="",
                     dashboard_password="", session_secret="")
        )
        status.clear()
        result = disabled(
            {"PATH_INFO": "/healthz", "REQUEST_METHOD": "GET"},
            lambda value, _headers: status.append(value),
        )
        self.assertEqual(
            ("503 Service Unavailable", b"Unavailable"),
            (status[0], b"".join(result)),
        )

    def test_enabled_connector_requires_complete_security_configuration(self):
        with patch.dict("os.environ", {"RRPP_INSTAGRAM_ENABLED": "true",
                                       "INSTAGRAM_VERIFY_TOKEN": "",
                                       "INSTAGRAM_APP_SECRET": "",
                                       "INSTAGRAM_BUSINESS_ACCOUNT_ID": "",
                                       "INSTAGRAM_WEBHOOK_ACCOUNT_ID": ""}, clear=False), \
                patch("rrpp_bridge.config.load_local_env"):
            with self.assertRaisesRegex(ValueError, "requires verify token"):
                Settings.from_env(require_auth=False)

    def test_webhook_receiver_id_is_independent_from_send_account_id(self):
        self.assertNotEqual(
            self.settings.instagram_webhook_account_id,
            self.settings.instagram_business_account_id,
        )
        self.assertEqual("200 OK", self.signed_request(payload())[0])
        conn = connect(self.path)
        self.assertEqual(1, conn.execute("SELECT count(*) FROM events").fetchone()[0])
        conn.close()

    def test_multiple_configured_receiver_ids_are_accepted_and_separated(self):
        settings = Settings(
            database_path=self.path, mode="shadow", dashboard_user="", dashboard_password="",
            session_secret="", instagram_enabled=True, instagram_verify_token="verify-me",
            instagram_app_secret="app-secret", instagram_accounts=(
                InstagramAccountSettings("primary", "ig-business-1", "ig-send-1"),
                InstagramAccountSettings("secondary", "ig-business-2", "ig-send-2"),
            ),
        )
        app = InstagramWebhookApplication(settings)
        self.assertEqual("200 OK", self.signed_request(payload(), app=app)[0])
        self.assertEqual("200 OK", self.signed_request(payload(
            message_id="ig-mid-2", sender="ig-user-1", recipient="ig-business-2"
        ), app=app)[0])
        conn = connect(self.path)
        self.assertEqual(
            [("ig-business-1", "ig-user-1"), ("ig-business-2", "ig-user-1")],
            [tuple(row) for row in conn.execute(
                "SELECT recipient,sender FROM events ORDER BY recipient"
            )],
        )
        self.assertEqual(2, conn.execute(
            "SELECT count(*) FROM receiver_accounts WHERE channel='instagram'"
        ).fetchone()[0])
        self.assertEqual(2, conn.execute("SELECT count(*) FROM conversations").fetchone()[0])
        conn.close()

    def test_multi_account_environment_uses_independent_token_variables(self):
        accounts = json.dumps([
            {"alias": "primary", "webhook_account_id": "ig-webhook-1",
             "business_account_id": "ig-send-1"},
            {"alias": "secondary", "webhook_account_id": "ig-webhook-2",
             "business_account_id": "ig-send-2"},
        ])
        with patch.dict("os.environ", {
            "RRPP_INSTAGRAM_ENABLED": "true",
            "RRPP_INSTAGRAM_SEND_ENABLED": "true",
            "INSTAGRAM_VERIFY_TOKEN": "verify",
            "INSTAGRAM_APP_SECRET": "secret",
            "INSTAGRAM_ACCOUNTS_JSON": accounts,
            "INSTAGRAM_ACCOUNT_PRIMARY_ACCESS_TOKEN": "token-primary",
            "INSTAGRAM_ACCOUNT_SECONDARY_ACCESS_TOKEN": "token-secondary",
        }, clear=True), patch("rrpp_bridge.config.load_local_env"):
            settings = Settings.from_env(require_auth=False)
        self.assertEqual(
            [("primary", "ig-webhook-1", "ig-send-1", "token-primary"),
             ("secondary", "ig-webhook-2", "ig-send-2", "token-secondary")],
            [(account.alias, account.webhook_account_id, account.business_account_id,
              account.access_token) for account in settings.configured_instagram_accounts()],
        )
        self.assertEqual(frozenset({"ig-webhook-1", "ig-webhook-2"}),
                         settings.instagram_webhook_account_ids())

    def test_legacy_single_account_environment_remains_supported(self):
        with patch.dict("os.environ", {
            "RRPP_INSTAGRAM_ENABLED": "true",
            "RRPP_INSTAGRAM_SEND_ENABLED": "true",
            "INSTAGRAM_VERIFY_TOKEN": "verify",
            "INSTAGRAM_APP_SECRET": "secret",
            "INSTAGRAM_WEBHOOK_ACCOUNT_ID": "ig-webhook-legacy",
            "INSTAGRAM_BUSINESS_ACCOUNT_ID": "ig-send-legacy",
            "INSTAGRAM_PAGE_ACCESS_TOKEN": "token-legacy",
        }, clear=True), patch("rrpp_bridge.config.load_local_env"):
            settings = Settings.from_env(require_auth=False)
        self.assertEqual(
            (InstagramAccountSettings(
                "legacy", "ig-webhook-legacy", "ig-send-legacy", "token-legacy"
            ),),
            settings.configured_instagram_accounts(),
        )

    def test_env_loader_accepts_alias_derived_account_token_keys(self):
        env_path = Path(self.tmp.name) / "multi-account.env"
        env_path.write_text("INSTAGRAM_ACCOUNT_PRIMARY_ACCESS_TOKEN=value\n", encoding="utf-8")
        with patch.dict("os.environ", {}, clear=True):
            load_local_env(env_path)
            self.assertEqual("value", os.environ[
                "INSTAGRAM_ACCOUNT_PRIMARY_ACCESS_TOKEN"
            ])

    def test_multi_account_configuration_rejects_mixed_duplicate_and_missing_tokens(self):
        valid = [
            {"alias": "primary", "webhook_account_id": "ig-webhook-1",
             "business_account_id": "ig-send-1"},
            {"alias": "secondary", "webhook_account_id": "ig-webhook-2",
             "business_account_id": "ig-send-2"},
        ]
        base = {
            "RRPP_INSTAGRAM_ENABLED": "true",
            "RRPP_INSTAGRAM_SEND_ENABLED": "true",
            "INSTAGRAM_VERIFY_TOKEN": "verify",
            "INSTAGRAM_APP_SECRET": "secret",
            "INSTAGRAM_ACCOUNTS_JSON": json.dumps(valid),
            "INSTAGRAM_ACCOUNT_PRIMARY_ACCESS_TOKEN": "token-primary",
            "INSTAGRAM_ACCOUNT_SECONDARY_ACCESS_TOKEN": "token-secondary",
        }
        with patch("rrpp_bridge.config.load_local_env"):
            with patch.dict("os.environ", {**base, "INSTAGRAM_BUSINESS_ACCOUNT_ID": "legacy"},
                            clear=True):
                with self.assertRaisesRegex(ValueError, "cannot be combined"):
                    Settings.from_env(require_auth=False)
            duplicate = [dict(valid[0]), {**valid[1], "webhook_account_id": "ig-webhook-1"}]
            with patch.dict("os.environ", {**base, "INSTAGRAM_ACCOUNTS_JSON": json.dumps(duplicate)},
                            clear=True):
                with self.assertRaisesRegex(ValueError, "must be unique"):
                    Settings.from_env(require_auth=False)
            inline_token = [{**valid[0], "access_token": "must-not-be-inline"}]
            with patch.dict("os.environ", {
                **base, "INSTAGRAM_ACCOUNTS_JSON": json.dumps(inline_token)
            }, clear=True):
                with self.assertRaisesRegex(ValueError, "contain only"):
                    Settings.from_env(require_auth=False)
            without_second_token = dict(base)
            without_second_token.pop("INSTAGRAM_ACCOUNT_SECONDARY_ACCESS_TOKEN")
            with patch.dict("os.environ", without_second_token, clear=True):
                with self.assertRaisesRegex(ValueError, "secondary requires"):
                    Settings.from_env(require_auth=False)

    def test_post_requires_valid_signature(self):
        body = json.dumps(payload()).encode()
        self.assertEqual("403 Forbidden", self.request("POST", body=body)[0])
        self.assertEqual("403 Forbidden", self.request(
            "POST", body=body, signature="sha256=" + "0" * 64)[0])
        conn = connect(self.path)
        self.assertEqual(0, conn.execute("SELECT count(*) FROM events").fetchone()[0])
        conn.close()

    def test_post_rejects_malformed_wrong_content_type_and_oversized_input(self):
        malformed = b"{not-json"
        signature = "sha256=" + hmac.new(b"app-secret", malformed, hashlib.sha256).hexdigest()
        self.assertEqual("400 Bad Request", self.request(
            "POST", body=malformed, signature=signature)[0])
        valid = json.dumps(payload()).encode()
        valid_signature = "sha256=" + hmac.new(
            b"app-secret", valid, hashlib.sha256).hexdigest()
        self.assertEqual("400 Bad Request", self.request(
            "POST", body=valid, signature=valid_signature, content_type="text/plain")[0])
        status: list[str] = []
        result = self.app({
            "REQUEST_METHOD": "POST", "PATH_INFO": "/webhooks/instagram",
            "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": "262145",
            "wsgi.input": io.BytesIO(),
        }, lambda value, _headers: status.append(value))
        self.assertEqual(("400 Bad Request", b"Invalid request"),
                         (status[0], b"".join(result)))

    def test_multiple_messages_in_one_delivery_are_all_enqueued(self):
        data = payload()
        data["entry"][0]["messaging"].append({
            "sender": {"id": "ig-user-2"}, "recipient": {"id": "ig-business-1"},
            "timestamp": 1_751_000_001_000,
            "message": {"mid": "ig-mid-2", "text": "Segon missatge"},
        })
        self.assertEqual("200 OK", self.signed_request(data)[0])
        conn = connect(self.path)
        self.assertEqual(2, conn.execute("SELECT count(*) FROM events").fetchone()[0])
        self.assertEqual(2, conn.execute("SELECT count(*) FROM jobs").fetchone()[0])
        self.assertEqual(2, conn.execute("SELECT count(*) FROM conversations").fetchone()[0])
        conn.close()

    def test_normalizes_and_persists_supported_message(self):
        events, sanitized, ignored = normalize(payload(), {"ig-business-1", "ig-business-2"})
        self.assertEqual(0, ignored)
        self.assertEqual(("instagram", "ig-mid-1", "ig-user-1", "ig-business-1"),
                         (events[0].channel, events[0].external_message_id,
                          events[0].sender, events[0].recipient))
        self.assertEqual("instagram:ig-business-1:ig-user-1", events[0].work_key)
        self.assertNotIn("time", sanitized["entries"][0])
        self.assertEqual(("200 OK", b"EVENT_RECEIVED"), self.signed_request(payload()))
        conn = connect(self.path)
        event = conn.execute("SELECT channel,sender,recipient,body_text FROM events").fetchone()
        receipt = conn.execute("SELECT * FROM inbound_webhook_receipts").fetchone()
        self.assertEqual(("instagram", "ig-user-1", "ig-business-1", "Hola, teniu entrades?"),
                         tuple(event))
        self.assertEqual((1, 0, "accepted"),
                         (receipt["accepted_count"], receipt["duplicate_count"], receipt["status"]))
        conn.close()

    def test_duplicate_message_does_not_create_duplicate_job(self):
        first = payload()
        second = payload()
        second["entry"][0]["time"] = 1_751_000_001
        self.assertEqual("200 OK", self.signed_request(first)[0])
        self.assertEqual("200 OK", self.signed_request(second)[0])
        conn = connect(self.path)
        self.assertEqual(1, conn.execute("SELECT count(*) FROM events").fetchone()[0])
        self.assertEqual(1, conn.execute("SELECT count(*) FROM jobs").fetchone()[0])
        self.assertEqual(1, conn.execute(
            "SELECT duplicate_count FROM inbound_webhook_receipts WHERE duplicate_count=1"
        ).fetchone()[0])
        conn.close()

    def test_account_centered_conversation_and_legacy_provider_never_send(self):
        class DraftProvider:
            provider_id = "fake-openclaw"

            def __init__(self):
                self.context = None

            def generate_action(self, context):
                self.context = context
                return IntendedAction("draft_reply", {"text": "Proposta Instagram"})

        conn = connect(self.path)
        venue_id = create_venue(conn, "Sala Instagram", "sala-instagram", "ca", "test")
        add_route(conn, venue_id, "instagram", "ig-business-1", "test")
        conn.close()
        self.signed_request(payload())
        conn = connect(self.path)
        set_mode(conn, "live", "test")
        provider = DraftProvider()
        with patch("urllib.request.urlopen") as network:
            self.assertTrue(Executor(conn, agent_provider=provider).run_once("worker.test"))
            network.assert_not_called()
        self.assertEqual(("instagram", "Hola, teniu entrades?"),
                         (provider.context.channel, provider.context.incoming_message))
        conversation = conn.execute("SELECT venue_id,status FROM conversations").fetchone()
        review = conn.execute(
            "SELECT r.kind,r.status,a.type FROM action_reviews r JOIN actions a ON a.id=r.action_id"
        ).fetchone()
        execution = conn.execute("SELECT status,simulated FROM action_executions").fetchone()
        self.assertEqual((None, "pending_review"), tuple(conversation))
        self.assertEqual(("draft", "pending", "escalate_to_owner"), tuple(review))
        self.assertEqual(("suppressed", 1), tuple(execution))
        conn.close()

    def test_wrong_recipient_is_ignored_and_remains_unpersisted_as_event(self):
        status, _ = self.signed_request(payload(recipient="another-account"))
        self.assertEqual("200 OK", status)
        conn = connect(self.path)
        self.assertEqual(0, conn.execute("SELECT count(*) FROM events").fetchone()[0])
        self.assertEqual(("ignored", 1), tuple(conn.execute(
            "SELECT status,ignored_count FROM inbound_webhook_receipts"
        ).fetchone()))
        conn.close()


if __name__ == "__main__":
    unittest.main()
