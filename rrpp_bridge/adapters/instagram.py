from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import NormalizedEvent


def _timestamp(value: object) -> str:
    if not isinstance(value, (int, float)) or value < 0:
        raise ValueError("Instagram message has an invalid timestamp")
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec="milliseconds")


def normalize(payload: dict[str, Any], business_account_id: str) -> tuple[list[NormalizedEvent], dict[str, Any], int]:
    if payload.get("object") != "instagram" or not isinstance(payload.get("entry"), list):
        raise ValueError("Unsupported Instagram webhook payload")
    events: list[NormalizedEvent] = []
    sanitized_entries: list[dict[str, Any]] = []
    ignored = 0
    for entry in payload["entry"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("messaging", []), list):
            raise ValueError("Malformed Instagram webhook entry")
        safe_messages: list[dict[str, Any]] = []
        for item in entry.get("messaging", []):
            if not isinstance(item, dict):
                raise ValueError("Malformed Instagram messaging event")
            sender = str((item.get("sender") or {}).get("id") or "").strip()
            recipient = str((item.get("recipient") or {}).get("id") or "").strip()
            message = item.get("message")
            if recipient != business_account_id:
                ignored += 1
                continue
            if not isinstance(message, dict) or message.get("is_echo") is True:
                ignored += 1
                continue
            message_id = str(message.get("mid") or "").strip()
            text = message.get("text")
            if not sender or not message_id or not isinstance(text, str) or not text.strip():
                ignored += 1
                continue
            if len(sender) > 200 or len(recipient) > 200 or len(message_id) > 500 or len(text) > 20_000:
                raise ValueError("Instagram message exceeds accepted limits")
            received_at = _timestamp(item.get("timestamp"))
            conversation_key = f"instagram:{recipient}:{sender}"
            safe_messages.append({"sender_id": sender, "recipient_id": recipient,
                                  "message_id": message_id, "text": text,
                                  "timestamp": item["timestamp"]})
            events.append(NormalizedEvent(
                channel="instagram", external_message_id=message_id, sender=sender,
                recipient=recipient, subject="Instagram DM", body_text=text.strip(),
                work_key=conversation_key, received_at=received_at,
                metadata={"native_message_id": message_id,
                          "native_conversation_id": conversation_key,
                          "instagram_sender_id": sender,
                          "instagram_recipient_id": recipient,
                          "raw_payload_reference": f"instagram:{message_id}"},
            ))
        sanitized_entries.append({"entry_id": str(entry.get("id") or ""),
                                  "messages": safe_messages})
    return events, {"object": "instagram", "entries": sanitized_entries}, ignored
