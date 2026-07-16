from __future__ import annotations

import json
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_RESPONSE_BYTES = 64 * 1024


class InstagramSendError(RuntimeError):
    def __init__(self, code: str, *, ambiguous: bool):
        super().__init__(code)
        self.code = code
        self.ambiguous = ambiguous


@dataclass(frozen=True)
class InstagramSendResult:
    recipient_id: str
    message_id: str


class InstagramSender:
    """Narrow official Send API client. It never logs request data or credentials."""

    def __init__(self, base_url: str, api_version: str, business_account_id: str,
                 access_token: str, timeout_seconds: float,
                 opener: Callable[..., Any] | None = None):
        self.endpoint = (
            f"{base_url.rstrip('/')}/{api_version}/{business_account_id}/messages"
        )
        self.access_token = access_token
        self.timeout_seconds = timeout_seconds
        self._opener = opener or urlopen

    def send_text(self, recipient_id: str, text: str) -> InstagramSendResult:
        recipient_id, text = recipient_id.strip(), text.strip()
        if not recipient_id or len(recipient_id) > 200 or not text or len(text) > 1_000:
            raise InstagramSendError("instagram_invalid_delivery", ambiguous=False)
        request = Request(
            self.endpoint,
            data=json.dumps({
                "recipient": {"id": recipient_id},
                "message": {"text": text},
            }, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            if 400 <= exc.code < 500:
                raise InstagramSendError("instagram_rejected", ambiguous=False) from exc
            raise InstagramSendError("instagram_delivery_unknown", ambiguous=True) from exc
        except (socket.timeout, TimeoutError, URLError, OSError) as exc:
            raise InstagramSendError("instagram_delivery_unknown", ambiguous=True) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise InstagramSendError("instagram_invalid_response", ambiguous=True)
        try:
            payload = json.loads(raw.decode("utf-8"))
            recipient = str(payload["recipient_id"]).strip()
            message = str(payload["message_id"]).strip()
            if not recipient or not message or len(recipient) > 200 or len(message) > 500:
                raise ValueError
        except (KeyError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise InstagramSendError("instagram_invalid_response", ambiguous=True) from exc
        return InstagramSendResult(recipient, message)


def build_instagram_sender(settings) -> InstagramSender | None:
    if not settings.instagram_send_enabled:
        return None
    return InstagramSender(
        settings.instagram_graph_base_url,
        settings.instagram_graph_api_version,
        settings.instagram_business_account_id,
        settings.instagram_page_access_token,
        settings.instagram_send_timeout_seconds,
    )
