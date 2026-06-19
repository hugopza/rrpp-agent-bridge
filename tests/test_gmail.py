from __future__ import annotations

import base64
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from rrpp_bridge.adapters.gmail import normalize
from rrpp_bridge.db import connect, initialize
from rrpp_bridge.gmail_connector import GMAIL_READONLY_SCOPE, GmailConnector


def gmail_message(message_id: str, body: str = "Hello from Gmail", *, html: bool = False):
    message = EmailMessage()
    message["From"] = "Customer <customer@example.com>"
    message["To"] = "Promoter <promoter@example.com>"
    message["Subject"] = "Event question"
    if html:
        message.set_content("<p>Hello <strong>from HTML</strong></p>", subtype="html")
    else:
        message.set_content(body)
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
    return {"id": message_id, "threadId": "thread-1", "internalDate": "1710000000000",
            "labelIds": ["INBOX", "UNREAD"], "raw": encoded}


class Request:
    def __init__(self, value=None, error: Exception | None = None):
        self.value, self.error = value, error

    def execute(self):
        if self.error:
            raise self.error
        return self.value


class FakeMessages:
    def __init__(self, messages, initial_ids):
        self.messages, self.initial_ids = messages, initial_ids

    def list(self, **kwargs):
        return Request({"messages": [{"id": item} for item in self.initial_ids]})

    def get(self, **kwargs):
        value = self.messages.get(kwargs["id"])
        if isinstance(value, Exception):
            return Request(error=value)
        return Request(value)


class FakeHistory:
    def __init__(self, pages):
        self.pages = list(pages)

    def list(self, **kwargs):
        return Request(self.pages.pop(0))


class FakeUsers:
    def __init__(self, messages, initial_ids, history_pages, profile_history="100"):
        self._messages = FakeMessages(messages, initial_ids)
        self._history = FakeHistory(history_pages)
        self.profile_history = profile_history

    def messages(self):
        return self._messages

    def history(self):
        return self._history

    def getProfile(self, **kwargs):
        return Request({"historyId": self.profile_history})


class FakeService:
    def __init__(self, messages, initial_ids=(), history_pages=()):
        self._users = FakeUsers(messages, initial_ids, history_pages)

    def users(self):
        return self._users


class GmailAdapterTests(unittest.TestCase):
    def test_normalizes_plain_text_without_retaining_raw_payload(self):
        event = normalize(gmail_message("gmail-1"))
        self.assertEqual("gmail", event.channel)
        self.assertEqual("gmail-1", event.external_message_id)
        self.assertEqual("gmail:thread-1", event.work_key)
        self.assertIn("Hello from Gmail", event.body_text)
        self.assertEqual("gmail:gmail-1", event.metadata["raw_payload_reference"])
        self.assertNotIn("raw", event.metadata)

    def test_extracts_html_text_and_rejects_malformed_payload(self):
        self.assertIn("Hello", normalize(gmail_message("html", html=True)).body_text)
        with self.assertRaises(ValueError):
            normalize({"id": "bad", "threadId": "thread", "raw": 42})

    def test_scope_is_strictly_read_only(self):
        self.assertEqual("https://www.googleapis.com/auth/gmail.readonly", GMAIL_READONLY_SCOPE)


class GmailConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "gmail.db")
        initialize(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_initial_sync_persists_before_cursor_and_is_idempotent(self):
        service = FakeService({"m1": gmail_message("m1")}, initial_ids=["m1"])
        result = GmailConnector(self.conn, service).poll_once()
        self.assertEqual((1, 0, "100"),
                         (result["accepted"], result["duplicates"], result["history_id"]))
        self.assertEqual("gmail", self.conn.execute("SELECT channel FROM events").fetchone()[0])
        self.assertEqual("100", self.conn.execute("SELECT value FROM connector_state").fetchone()[0])

    def test_incremental_history_advances_after_persistence(self):
        self.conn.execute(
            "INSERT INTO connector_state VALUES('gmail','history_id','100','now')"
        )
        page = {"historyId": "101", "history": [
            {"messagesAdded": [{"message": {"id": "m2", "labelIds": ["INBOX"]}}]}
        ]}
        service = FakeService({"m2": gmail_message("m2")}, history_pages=[page])
        result = GmailConnector(self.conn, service).poll_once()
        self.assertEqual("101", result["history_id"])
        self.assertEqual(1, self.conn.execute("SELECT count(*) FROM events").fetchone()[0])

    def test_fetch_failure_does_not_advance_cursor(self):
        service = FakeService({"bad": RuntimeError("controlled")}, initial_ids=["bad"])
        with self.assertRaises(RuntimeError):
            GmailConnector(self.conn, service).poll_once()
        self.assertEqual(0, self.conn.execute("SELECT count(*) FROM connector_state").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
