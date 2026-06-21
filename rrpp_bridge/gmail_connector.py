from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .adapters.gmail import normalize
from .audit import record, utc_now
from .db import transaction
from .queue import JobQueue
from .operations import heartbeat

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CONNECTOR = "gmail"
HISTORY_KEY = "history_id"


def _write_token(token_path: Path, content: str) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = token_path.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(token_path)


def authorize(client_path: Path, token_path: Path) -> None:
    if not client_path.is_file():
        raise ValueError(f"Gmail OAuth client file not found: {client_path}")
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), [GMAIL_READONLY_SCOPE])
    credentials = flow.run_local_server(port=0, open_browser=True,
                                        authorization_prompt_message="Opening Gmail authorization...")
    _write_token(token_path, credentials.to_json())


def credentials(token_path: Path):
    if not token_path.is_file():
        raise ValueError("Gmail is not authorized; run `rrpp-bridge gmail-auth`")
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    result = Credentials.from_authorized_user_file(str(token_path), [GMAIL_READONLY_SCOPE])
    if result.expired and result.refresh_token:
        result.refresh(Request())
        _write_token(token_path, result.to_json())
    if not result.valid:
        raise ValueError("Gmail authorization is invalid or expired")
    return result


def build_service(token_path: Path):
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=credentials(token_path), cache_discovery=False)


class GmailConnector:
    def __init__(self, conn: sqlite3.Connection, service: Any, batch_size: int = 50):
        self.conn = conn
        self.service = service
        self.batch_size = batch_size
        self.queue = JobQueue(conn)

    def _state(self) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM connector_state WHERE connector=? AND key=?",
            (CONNECTOR, HISTORY_KEY),
        ).fetchone()
        return str(row["value"]) if row else None

    def _set_state(self, history_id: str) -> None:
        with transaction(self.conn, immediate=True):
            self.conn.execute(
                "INSERT INTO connector_state(connector,key,value,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(connector,key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (CONNECTOR, HISTORY_KEY, history_id, utc_now()),
            )
            record(self.conn, "connector.gmail", "cursor.advanced", "connector", CONNECTOR,
                   "updated", {"cursor_type": "history_id"})

    def _fetch(self, message_id: str) -> dict[str, Any]:
        return self.service.users().messages().get(
            userId="me", id=message_id, format="raw"
        ).execute()

    def _persist_ids(self, message_ids: list[str]) -> tuple[int, int]:
        accepted = duplicates = 0
        for message_id in dict.fromkeys(message_ids):
            _, created = self.queue.enqueue(normalize(self._fetch(message_id)))
            if created:
                accepted += 1
            else:
                duplicates += 1
        return accepted, duplicates

    def _initial_sync(self) -> tuple[int, int, str]:
        starting_history = str(self.service.users().getProfile(userId="me").execute()["historyId"])
        response = self.service.users().messages().list(
            userId="me", labelIds=["INBOX"], maxResults=self.batch_size
        ).execute()
        ids = [str(item["id"]) for item in response.get("messages", []) if item.get("id")]
        accepted, duplicates = self._persist_ids(list(reversed(ids)))
        return accepted, duplicates, starting_history

    def _incremental_sync(self, history_id: str) -> tuple[int, int, str]:
        ids: list[str] = []
        page_token: str | None = None
        latest = history_id
        while True:
            request = self.service.users().history().list(
                userId="me", startHistoryId=history_id, historyTypes=["messageAdded"],
                labelId="INBOX", pageToken=page_token,
            )
            response = request.execute()
            latest = str(response.get("historyId") or latest)
            for history in response.get("history", []):
                for added in history.get("messagesAdded", []):
                    message = added.get("message") or {}
                    if message.get("id"):
                        ids.append(str(message["id"]))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        accepted, duplicates = self._persist_ids(ids)
        return accepted, duplicates, latest

    def poll_once(self) -> dict[str, int | str]:
        cursor = self._state()
        try:
            accepted, duplicates, next_cursor = (
                self._incremental_sync(cursor) if cursor else self._initial_sync()
            )
        except Exception as exc:
            # Gmail returns 404 for history IDs outside its retention window.
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status != 404 or cursor is None:
                record(self.conn, "connector.gmail", "poll.failed", "connector", CONNECTOR,
                       "failed", {"error_code": type(exc).__name__})
                raise
            accepted, duplicates, next_cursor = self._initial_sync()
        self._set_state(next_cursor)
        record(self.conn, "connector.gmail", "poll.completed", "connector", CONNECTOR,
               "completed", {"accepted": accepted, "duplicates": duplicates})
        return {"accepted": accepted, "duplicates": duplicates, "history_id": next_cursor}


def run_poll_loop(conn: sqlite3.Connection, service: Any, batch_size: int,
                  poll_seconds: int, once: bool = False, instance: str = "") -> None:
    connector = GmailConnector(conn, service, batch_size)
    while True:
        try:
            if instance:
                heartbeat(conn, "gmail", instance)
            result = connector.poll_once()
            if instance:
                heartbeat(conn, "gmail", instance, success=True,
                          details={"accepted": result["accepted"], "duplicates": result["duplicates"]})
            print(json.dumps(result, sort_keys=True))
            if once:
                return
        except Exception as exc:
            if instance:
                heartbeat(conn, "gmail", instance, error=exc)
            print(json.dumps({"connector": "gmail", "status": "failed",
                              "error_code": type(exc).__name__}, sort_keys=True))
            if once:
                raise
        time.sleep(poll_seconds)
