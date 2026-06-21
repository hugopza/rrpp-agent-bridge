from __future__ import annotations

import base64
from datetime import datetime, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Any

from ..models import NormalizedEvent

MAX_BODY_LENGTH = 20_000


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _content(part: Message) -> str:
    try:
        value = part.get_content()
    except (LookupError, UnicodeError, ValueError):
        return ""
    return value if isinstance(value, str) else ""


def _body(message: Message) -> str:
    plain: list[str] = []
    html_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain.append(_content(part))
        elif content_type == "text/html":
            html_parts.append(_content(part))
    text = "\n\n".join(item.strip() for item in plain if item.strip())
    if not text and html_parts:
        parser = _TextExtractor()
        parser.feed("\n".join(html_parts))
        text = "\n".join(parser.parts)
    return text[:MAX_BODY_LENGTH] or "[Message has no readable text body]"


def _addresses(message: Message, header: str, fallback: str) -> str:
    addresses = [address.casefold() for _, address in getaddresses(message.get_all(header, [])) if address]
    return ",".join(addresses)[:500] or fallback


def normalize(raw_message: dict[str, Any]) -> NormalizedEvent:
    message_id = str(raw_message.get("id") or "").strip()
    thread_id = str(raw_message.get("threadId") or message_id).strip()
    encoded = raw_message.get("raw")
    if not message_id or not thread_id or not isinstance(encoded, str):
        raise ValueError("Gmail message is missing its stable identity or raw payload")
    try:
        padding = "=" * (-len(encoded) % 4)
        parsed = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(encoded + padding)
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("Gmail message payload is malformed") from exc
    sender = _addresses(parsed, "From", "[unknown sender]")
    recipient = _addresses(parsed, "To", "[unknown recipient]")
    subject = str(parsed.get("Subject") or "")[:500]
    internal_date = str(raw_message.get("internalDate") or "")
    try:
        received_at = datetime.fromtimestamp(int(internal_date) / 1000, timezone.utc).isoformat(
            timespec="milliseconds"
        )
    except (ValueError, TypeError, OverflowError):
        received_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    labels = raw_message.get("labelIds") or []
    safe_labels = [str(label)[:100] for label in labels if isinstance(label, str)][:20]
    return NormalizedEvent(
        channel="gmail", external_message_id=message_id, sender=sender,
        recipient=recipient, subject=subject, body_text=_body(parsed),
        work_key=f"gmail:{thread_id}",
        metadata={"thread_id": thread_id, "label_ids": safe_labels,
                  "raw_payload_reference": f"gmail:{message_id}"},
        received_at=received_at,
    )
